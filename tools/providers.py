"""
tools/providers.py — PriceProvider interface + concrete implementations (bài 21).

Providers (VN stocks go through the shared vnmarket client: throttle, retry, cache, fallback):
  FireantProvider    — OHLCV + foreign volumes/values for ingest (nghìn đồng)
  VciDirectProvider  — VND bars, batch bars, live foreign board
  KbsProvider        — second reader of the same daily bars
  TcbsDirectProvider — TCBS public API (endpoint dead; unused)
  FallbackProvider   — wraps two providers, tries primary first
  YFinanceProvider   — yfinance cho mã NYSE/NASDAQ
  SsiIndexProvider   — VNINDEX/VN30/HNX/UPCOM with matched value (VCI chart)

Class names predate vnmarket and are kept for callers/tests.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import pandas as pd


# ── TTL cache ─────────────────────────────────────────────────────────────────

_TTL_PRICE = 5 * 60     # 5 min
_TTL_HISTORY = 60 * 60  # 1 hr
_TTL_LIVE = 60          # in-session: today's bar still moves, keep the cache short


def _in_trading_hours() -> bool:
    """Trading day 09:00-15:30 local (VN) - window where today's daily bar is still changing."""
    now = datetime.now()
    if now.weekday() >= 5 or not ((9, 0) <= (now.hour, now.minute) < (15, 30)):
        return False
    from tools.vn_holidays import is_vn_holiday
    return not is_vn_holiday(now.date().isoformat())


class _Miss:
    def __repr__(self) -> str:
        return "<MISS>"


_MISS = _Miss()


class _TTLCache:
    def __init__(self) -> None:
        self._store: dict[Any, tuple[Any, float]] = {}

    def get(self, key: Any) -> Any:
        entry = self._store.get(key)
        if entry is None:
            return _MISS
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return _MISS
        return value

    def set(self, key: Any, value: Any, ttl: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    def clear(self) -> None:
        self._store.clear()


_price_cache = _TTLCache()
_history_cache = _TTLCache()


# ── Interface ─────────────────────────────────────────────────────────────────

class PriceProvider(ABC):
    """Swap-able data source for price and OHLCV data."""

    @abstractmethod
    def fetch_price(self, ticker: str) -> float:
        """Fetch live price — called only on cache miss."""
        ...

    @abstractmethod
    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        """Fetch OHLCV history — called only on cache miss."""
        ...

    def get_price(self, ticker: str) -> float:
        key = (self.__class__.__name__, "price", ticker)
        cached = _price_cache.get(key)
        if not isinstance(cached, _Miss):
            return cached
        value = self.fetch_price(ticker)
        _price_cache.set(key, value, _TTL_LIVE if _in_trading_hours() else _TTL_PRICE)
        return value

    def get_history(self, ticker: str, days: int) -> pd.DataFrame:
        key = (self.__class__.__name__, "history", ticker, days)
        cached = _history_cache.get(key)
        if not isinstance(cached, _Miss):
            return cached
        value = self.fetch_history(ticker, days)
        _history_cache.set(key, value, _TTL_LIVE if _in_trading_hours() else _TTL_HISTORY)
        return value


# ── Price unit normalization ──────────────────────────────────────────────────
# Tool-facing unit for VN stock prices is VND (VCI/TCBS/yfinance return VND natively).
# Fireant and KBS return nghìn đồng (VCB 56.7 = 56,700 VND), so their fetch_price /
# fetch_history multiply by this factor. ohlcv_daily stores nghìn đồng (see
# tools/ohlcv_db.py), and ingest reads Fireant via fetch_history_range (raw, unscaled).
_THOUSAND_VND = 1000.0
_PRICE_COLS = ("open", "high", "low", "close")


def _thousands_to_vnd(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in _PRICE_COLS:
        if col in df.columns:
            df[col] = df[col] * _THOUSAND_VND
    return df


# ── vnmarket-backed providers ─────────────────────────────────────────────────
# FireantProvider / VciDirectProvider / KbsProvider keep their names and public methods (callers,
# tests and ingest import them) but every call now goes through the shared vnmarket client, which
# supplies throttling, retry, caching and source fallback (VCI -> KBS -> DNSE -> SSI -> HOSE).

def _vn():
    from core.vnmarket_client import get_client
    return get_client()


def _bars(ticker: str, count: int) -> pd.DataFrame:
    """Last `count` daily bars, VND, `time` as 'YYYY-MM-DD' strings."""
    df = _vn().prices.history(ticker.upper(), count=count)
    df = df.copy()
    df["time"] = df["time"].dt.strftime("%Y-%m-%d")
    return df[["time", "open", "high", "low", "close", "volume"]].drop_duplicates(subset=["time"]).reset_index(drop=True)


class FireantProvider(PriceProvider):
    """
    OHLCV + foreign volumes via vnmarket (no Fireant credentials needed).

    `fetch_history_range()` returns extra columns foreign_buy_vol / foreign_sell_vol and
    foreign_buy_value / foreign_sell_value (raw VND, NaN on days the source lacks them)
    (all omitted when the foreign-flow sources fail) so ingest scripts can upsert foreign_flows
    in the same pass. Prices are in nghìn đồng (the ohlcv_daily unit); fetch_price /
    fetch_history convert to VND.
    """

    def fetch_history_range(
        self, ticker: str, start_date: str, end_date: str
    ) -> "pd.DataFrame":
        """
        Returns DataFrame with columns:
            time, open, high, low, close, volume[, foreign_buy_vol, foreign_sell_vol]
        Sorted oldest → newest, duplicates removed. Raises ValueError when empty.
        """
        vn = _vn()
        px = vn.prices.history(ticker.upper(), start=start_date, end=end_date)
        px = px.copy()
        px["time"] = px["time"].dt.strftime("%Y-%m-%d")
        df = px[["time", "open", "high", "low", "close", "volume"]].copy()
        for col in _PRICE_COLS:
            df[col] = df[col] / _THOUSAND_VND   # VND → nghìn đồng
        df["volume"] = df["volume"].astype(int)
        if df.empty:
            raise ValueError(f"vnmarket: no data for '{ticker}' {start_date}..{end_date}")
        try:
            fg = vn.market.foreign(ticker.upper(), start=start_date, end=end_date, limit=1000)
            fg = fg.assign(time=fg["date"].dt.strftime("%Y-%m-%d"))
            fg = fg[["time", "buy_volume", "sell_volume", "buy_value", "sell_value"]].rename(columns={
                "buy_volume": "foreign_buy_vol", "sell_volume": "foreign_sell_vol",
                "buy_value": "foreign_buy_value", "sell_value": "foreign_sell_value"})   # values: raw VND
            df = df.merge(fg, on="time", how="left")
            vol_cols = ["foreign_buy_vol", "foreign_sell_vol"]
            df[vol_cols] = df[vol_cols].fillna(0).astype(int)
        except Exception:
            pass   # prices without foreign columns; caller checks "foreign_buy_vol" in df.columns
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def fetch_price(self, ticker: str) -> float:
        return float(_bars(ticker, 5)["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return _bars(ticker, days + 10).tail(days).reset_index(drop=True)


# ── VciDirectProvider ─────────────────────────────────────────────────────────

class VciDirectProvider(PriceProvider):
    """VND OHLCV, batch bars and live foreign board via vnmarket (VCI primary)."""

    def _fetch_ohlcv(self, ticker: str, count_back: int) -> pd.DataFrame:
        return _bars(ticker, count_back)

    def fetch_price(self, ticker: str) -> float:
        return float(self._fetch_ohlcv(ticker, count_back=5)["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return self._fetch_ohlcv(ticker, count_back=days + 10).tail(days).reset_index(drop=True)

    def fetch_batch_latest(self, tickers: list[str], count_back: int = 2) -> dict[str, pd.DataFrame]:
        """Latest OHLCV for multiple tickers. Concurrent single-ticker calls (max 20 workers);
        vnmarket throttles per source, so bursts queue instead of getting blocked."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_one(ticker: str) -> tuple[str, pd.DataFrame | None]:
            try:
                return ticker.upper(), _bars(ticker, count_back)
            except Exception:
                return ticker, None

        result: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                sym, df = future.result()
                if df is not None and not df.empty:
                    result[sym] = df
        return result

    def fetch_foreign_batch(self, tickers: list[str]) -> list[dict]:
        """
        Live foreign buy/sell (raw VND values) from the vnmarket price board.
        Returns list of {ticker, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume}.
        Raises on source failure — callers should catch.
        """
        board = _vn().market.quotes([t.upper() for t in tickers])
        rows = []
        for r in board.itertuples():
            buy_val = float(r.foreign_buy_value or 0)
            sell_val = float(r.foreign_sell_value or 0)
            buy_vol = int(r.foreign_buy_volume or 0)
            sell_vol = int(r.foreign_sell_volume or 0)
            rows.append({
                "ticker": str(r.symbol).upper(),
                "buy_value": buy_val,
                "sell_value": sell_val,
                "net_value": buy_val - sell_val,
                "buy_volume": buy_vol,
                "sell_volume": sell_vol,
                "net_volume": buy_vol - sell_vol,
            })
        return rows


# ── TcbsDirectProvider ───────────────────────────────────────────────────────

class TcbsDirectProvider(PriceProvider):
    """
    TCBS public API — no auth, no vnstock.
    GET https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term
    VN stock tickers only (not indices).
    """

    _URL = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    def _fetch_ohlcv(self, ticker: str, count_back: int) -> pd.DataFrame:
        import httpx

        to_ts = int(datetime.now().timestamp())
        from_ts = to_ts - count_back * 2 * 86400  # 2x days buffer for weekends/holidays
        params = {
            "ticker": ticker.upper(),
            "type": "stock",
            "resolution": "D",
            "from": from_ts,
            "to": to_ts,
        }
        resp = httpx.get(self._URL, params=params, headers=self._HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        bars = data.get("data") or []
        if not bars:
            raise ValueError(f"No TCBS data for '{ticker}'")

        rows = []
        for bar in bars:
            td = bar.get("tradingDate") or ""
            date_str = td[:10] if td else ""
            if not date_str:
                continue
            rows.append({
                "time":   date_str,
                "open":   float(bar.get("open") or 0),
                "high":   float(bar.get("high") or 0),
                "low":    float(bar.get("low") or 0),
                "close":  float(bar.get("close") or 0),
                "volume": int(bar.get("volume") or 0),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            raise ValueError(f"Empty TCBS bars for '{ticker}'")
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def fetch_price(self, ticker: str) -> float:
        df = self._fetch_ohlcv(ticker, count_back=5)
        if df.empty:
            raise ValueError(f"No TCBS price for '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        df = self._fetch_ohlcv(ticker, count_back=days + 10)
        if df.empty:
            raise ValueError(f"No TCBS history for '{ticker}'")
        return df.tail(days).reset_index(drop=True)


# ── FallbackProvider ──────────────────────────────────────────────────────────

class FallbackProvider(PriceProvider):
    """Try primary provider; on any exception fall back to secondary."""

    def __init__(self, primary: PriceProvider, secondary: PriceProvider) -> None:
        self._primary = primary
        self._secondary = secondary

    def fetch_price(self, ticker: str) -> float:
        try:
            return self._primary.fetch_price(ticker)
        except Exception:
            return self._secondary.fetch_price(ticker)

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        try:
            return self._primary.fetch_history(ticker, days)
        except Exception:
            return self._secondary.fetch_history(ticker, days)


# ── KbsProvider ──────────────────────────────────────────────────────────────

class KbsProvider(PriceProvider):
    """Second-opinion daily bars via vnmarket (same chain; kept so adjusted-price merges still have two readers)."""

    def fetch_price(self, ticker: str) -> float:
        return float(_bars(ticker, 5)["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return _bars(ticker, days + 10).tail(days).reset_index(drop=True)


# ── YFinanceProvider ──────────────────────────────────────────────────────────

class YFinanceProvider(PriceProvider):
    """Dùng yfinance cho mã NYSE/NASDAQ (AAPL, TSLA, NVDA...). Trả giá USD."""

    def fetch_price(self, ticker: str) -> float:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            raise ValueError(f"Không có dữ liệu cho '{ticker}'")
        return float(hist["Close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=f"{days + 10}d")
        if hist.empty:
            raise ValueError(f"Không có dữ liệu cho '{ticker}'")
        hist = hist.reset_index()
        hist = hist.rename(columns={
            "Date": "time", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        return hist[["time", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)


# ── SsiIndexProvider (now backed by VCI) ─────────────────────────────────────
#
# SSI iBoard API (iboard-query.ssi.com.vn/v2/stock/second-chart) returns 404
# for all paths as of 2026-08. VCI's OHLCChart endpoint serves VNINDEX/VN30/HNX
# with the same columnar format as stock tickers, plus accumulatedValue (triệu VND).

_SSI_INDEX_CODES = frozenset({"VNINDEX", "VN30", "HNX", "HNX30", "UPCOM"})


class SsiIndexProvider(PriceProvider):
    """
    Fetch VNINDEX/HNX/UPCOM OHLCV via VCI OHLCChart endpoint (same as VciDirectProvider).
    Returns DataFrame with extra column `accumulated_value_vnd` (raw VND) for matched value.
    """

    _URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    _HEADERS = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    def _fetch_ohlcv(self, symbol: str, count_back: int) -> pd.DataFrame:
        import httpx

        payload = {
            "timeFrame": "ONE_DAY",
            "symbols": [symbol.upper()],
            "to": int(datetime.now().timestamp()),
            "countBack": count_back,
        }
        resp = httpx.post(self._URL, json=payload, headers=self._HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not data:
            raise ValueError(f"No data from VCI for index '{symbol}'")

        item = data[0]
        if not item.get("t"):
            raise ValueError(f"Empty timeseries from VCI for index '{symbol}'")

        # accumulatedValue unit: triệu VND → multiply by 1e6 for raw VND
        acc_vals = item.get("accumulatedValue") or []

        rows = [
            {
                "time":                   datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
                "open":                   item["o"][i],
                "high":                   item["h"][i],
                "low":                    item["l"][i],
                "close":                  item["c"][i],
                "volume":                 item["v"][i],
                "accumulated_value_vnd":  float(acc_vals[i]) * 1_000_000 if i < len(acc_vals) else 0.0,
            }
            for i, ts in enumerate(item["t"])
        ]
        df = pd.DataFrame(rows)
        # The endpoint changed unit mid-history: candles up to 2025-08-11 already come in VND,
        # so the ×1e6 above inflates them (1.3e19). No session trades 1e16 VND (10 triệu tỷ).
        df.loc[df["accumulated_value_vnd"] > 1e16, "accumulated_value_vnd"] /= 1_000_000
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def fetch_price(self, ticker: str) -> float:
        df = self._fetch_ohlcv(ticker, count_back=5)
        if df.empty:
            raise ValueError(f"No price from VCI index for '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        df = self._fetch_ohlcv(ticker, count_back=days + 10)
        if df.empty:
            raise ValueError(f"No history from VCI index for '{ticker}'")
        return df.tail(days).reset_index(drop=True)


# ── Provider selection ────────────────────────────────────────────────────────

# VN broad indices that use SsiIndexProvider (real data, not VN30 proxy).
_VN_INDEX_ALIASES: dict[str, str] = {
    "VN-INDEX": "VNINDEX",
    "HOSE":     "VNINDEX",
    "VN100":    "VN30",    # VN100 → VN30 proxy (SSI has VN30 too)
}

# Indices served by SsiIndexProvider directly (no alias needed, use as-is).
_SSI_DIRECT = frozenset({"VNINDEX", "VN30", "HNX", "HNX30", "UPCOM"})


def resolve_ticker(ticker: str) -> str:
    """Map VN index aliases to canonical SSI/VCI symbols. Stock tickers pass through."""
    upper = ticker.strip().upper()
    return _VN_INDEX_ALIASES.get(upper, upper)


_vn_ticker_cache: set[str] | None = None


def _vn_ticker_set() -> set[str]:
    """Lazy-load VN ticker universe from securities table (cached)."""
    global _vn_ticker_cache
    if _vn_ticker_cache is not None:
        return _vn_ticker_cache
    try:
        from core.tickers import get_tickers
        tickers = get_tickers()
        if tickers:
            _vn_ticker_cache = set(tickers)
            return _vn_ticker_cache
    except Exception:
        pass
    _vn_ticker_cache = {"HPG", "VCB", "FPT", "VNM", "TCB", "MBB", "VHM"}
    return _vn_ticker_cache


def _detect_provider(ticker: str) -> PriceProvider:
    """
    Provider routing:
    - VN broad indices (VNINDEX/VN30/HNX/HNX30/UPCOM) → SsiIndexProvider
    - 2–4 chars, no dot, in VN universe → VciDirectProvider (VN stock tickers)
    - dot or >4 chars or not in VN universe → YFinanceProvider (international, USD)
    """
    resolved = resolve_ticker(ticker)
    if resolved in _SSI_DIRECT:
        return SsiIndexProvider()
    if "." not in resolved and len(resolved) <= 4:
        vn_tickers = _vn_ticker_set()
        if resolved in vn_tickers or not vn_tickers:
            return FallbackProvider(
                FireantProvider(),
                FallbackProvider(VciDirectProvider(), KbsProvider()),  # TCBS endpoint 404s (dead)
            )
    return YFinanceProvider()
