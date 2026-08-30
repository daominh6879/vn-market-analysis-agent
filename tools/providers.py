"""
tools/providers.py — PriceProvider interface + concrete implementations (bài 21).

Providers:
  FireantProvider    — Fireant REST API, primary for OHLCV + foreign volumes
  VciDirectProvider  — VCI REST API, không dùng vnstock
  TcbsDirectProvider — TCBS public API, fallback for VN stocks
  FallbackProvider   — wraps two providers, tries primary first
  YFinanceProvider   — yfinance cho mã NYSE/NASDAQ

Provider priority for VN stocks: Fireant → VCI → TCBS
Không import vnstock ở đây.
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
        _price_cache.set(key, value, _TTL_PRICE)
        return value

    def get_history(self, ticker: str, days: int) -> pd.DataFrame:
        key = (self.__class__.__name__, "history", ticker, days)
        cached = _history_cache.get(key)
        if not isinstance(cached, _Miss):
            return cached
        value = self.fetch_history(ticker, days)
        _history_cache.set(key, value, _TTL_HISTORY)
        return value


# ── FireantProvider ───────────────────────────────────────────────────────────

class FireantProvider(PriceProvider):
    """
    Fireant REST API — primary source for VN stock OHLCV + foreign volumes.

    Auth: POST {FIREANT_BASE}/authentication/login → accessToken (Bearer, cached).
    Data: GET  {FIREANT_BASE}/symbols/{symbol}/historical-quotes
    Response fields used: date, priceOpen, priceHigh, priceLow, priceClose,
                          dealVolume, buyForeignQuantity, sellForeignQuantity.

    `fetch_history_range()` returns extra columns foreign_buy_vol / foreign_sell_vol
    so ingest scripts can upsert foreign_flows in the same pass.
    """

    _token: str | None = None  # class-level token shared across instances

    def __init__(self) -> None:
        import os
        self._base = os.getenv("FIREANT_BASE", "").rstrip("/")
        self._email = os.getenv("FIREANT_EMAIL", "")
        self._password = os.getenv("FIREANT_PASSWORD", "")

    def _login(self) -> str:
        import httpx
        if not self._base or not self._email:
            raise ValueError("FIREANT_BASE / FIREANT_EMAIL not set in env")
        resp = httpx.post(
            f"{self._base}/authentication/login",
            json={"email": self._email, "password": self._password, "rememberMe": True},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("accessToken") or (body.get("data") or {}).get("accessToken")
        if not token:
            raise ValueError(f"Fireant login: no accessToken in response: {body}")
        FireantProvider._token = token
        return token

    def _get_token(self) -> str:
        if FireantProvider._token:
            return FireantProvider._token
        return self._login()

    def fetch_history_range(
        self, ticker: str, start_date: str, end_date: str
    ) -> "pd.DataFrame":
        """
        Fetch OHLCV + foreign volumes for a date range.

        Returns DataFrame with columns:
            time, open, high, low, close, volume,
            foreign_buy_vol, foreign_sell_vol
        Sorted oldest → newest, duplicates removed.
        """
        import httpx

        days = (
            datetime.strptime(end_date, "%Y-%m-%d")
            - datetime.strptime(start_date, "%Y-%m-%d")
        ).days
        limit = int(days * 252 / 365) + 20

        def _request(token: str) -> httpx.Response:
            return httpx.get(
                f"{self._base}/symbols/{ticker.upper()}/historical-quotes",
                params={"startDate": start_date, "endDate": end_date, "offset": 0, "limit": limit},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )

        token = self._get_token()
        resp = _request(token)
        if resp.status_code == 401:
            FireantProvider._token = None
            resp = _request(self._login())
        resp.raise_for_status()

        data = resp.json()
        if not data:
            raise ValueError(f"Fireant: no data for '{ticker}' {start_date}..{end_date}")

        rows = [
            {
                "time":             str(d["date"])[:10],
                "open":             float(d.get("priceOpen") or 0),
                "high":             float(d.get("priceHigh") or 0),
                "low":              float(d.get("priceLow") or 0),
                "close":            float(d.get("priceClose") or 0),
                "volume":           int(d.get("dealVolume") or 0),
                "foreign_buy_vol":  int(d.get("buyForeignQuantity") or 0),
                "foreign_sell_vol": int(d.get("sellForeignQuantity") or 0),
            }
            for d in data
        ]
        df = pd.DataFrame(rows)
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def fetch_price(self, ticker: str) -> float:
        from datetime import date, timedelta
        today = date.today()
        df = self.fetch_history_range(ticker, str(today - timedelta(days=10)), str(today))
        if df.empty:
            raise ValueError(f"Fireant: no price for '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        from datetime import date, timedelta
        today = date.today()
        df = self.fetch_history_range(
            ticker,
            str(today - timedelta(days=days + 10)),
            str(today),
        )
        if df.empty:
            raise ValueError(f"Fireant: no history for '{ticker}'")
        return df.tail(days).reset_index(drop=True)


# ── VciDirectProvider ─────────────────────────────────────────────────────────

class VciDirectProvider(PriceProvider):
    """
    Gọi thẳng VCI REST API — không import vnstock.
    POST https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart
    Trả giá VND cho mã chứng khoán Việt Nam.
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

    def _fetch_ohlcv(self, ticker: str, count_back: int) -> pd.DataFrame:
        import httpx

        payload = {
            "timeFrame": "ONE_DAY",
            "symbols": [ticker.upper()],
            "to": int(datetime.now().timestamp()),
            "countBack": count_back,
        }
        resp = httpx.post(self._URL, json=payload, headers=self._HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not data:
            raise ValueError(f"Không có dữ liệu cho '{ticker}'")

        symbol_data = data[0]
        # VCI returns columnar arrays: {o, h, l, c, v, t}
        rows = [
            {
                "time":   datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
                "open":   symbol_data["o"][i],
                "high":   symbol_data["h"][i],
                "low":    symbol_data["l"][i],
                "close":  symbol_data["c"][i],
                "volume": symbol_data["v"][i],
            }
            for i, ts in enumerate(symbol_data["t"])
        ]
        df = pd.DataFrame(rows)
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def fetch_price(self, ticker: str) -> float:
        df = self._fetch_ohlcv(ticker, count_back=5)
        if df.empty:
            raise ValueError(f"Không có dữ liệu giá cho '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        df = self._fetch_ohlcv(ticker, count_back=days + 10)
        if df.empty:
            raise ValueError(f"Không có dữ liệu lịch sử cho '{ticker}'")
        return df.tail(days).reset_index(drop=True)

    def fetch_batch_latest(self, tickers: list[str], count_back: int = 2) -> dict[str, pd.DataFrame]:
        """Fetch latest OHLCV for multiple tickers. Uses concurrent single-ticker requests.

        VCI gap-chart only supports 1 symbol per call; multi-symbol returns [].
        Uses ThreadPoolExecutor to fetch in parallel (max 20 concurrent).
        """
        import httpx
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_one(ticker: str) -> tuple[str, pd.DataFrame | None]:
            payload = {
                "timeFrame": "ONE_DAY",
                "symbols": [ticker.upper()],
                "to": int(datetime.now().timestamp()),
                "countBack": count_back,
            }
            try:
                resp = httpx.post(self._URL, json=payload, headers=self._HEADERS, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                if not data:
                    return ticker, None
                symbol_data = data[0]
                if not symbol_data.get("t"):
                    return ticker, None
                rows = [
                    {
                        "time":   datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d"),
                        "open":   symbol_data["o"][i],
                        "high":   symbol_data["h"][i],
                        "low":    symbol_data["l"][i],
                        "close":  symbol_data["c"][i],
                        "volume": symbol_data["v"][i],
                    }
                    for i, ts in enumerate(symbol_data["t"])
                ]
                df = pd.DataFrame(rows).sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
                return ticker.upper(), df
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
        Fetch live foreign buy/sell values for a batch of tickers via VCI price board.

        Endpoint: POST https://trading.vietcap.com.vn/api/price/symbols/getList
        Returns list of {ticker, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume}.
        Raises on network error — callers should catch.
        """
        import httpx

        url = "https://trading.vietcap.com.vn/api/price/symbols/getList"
        payload = {"symbols": [t.upper() for t in tickers]}
        resp = httpx.post(url, json=payload, headers=self._HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not data:
            return []

        rows = []
        for item in data:
            # ticker from listingInfo.symbol
            listing = item.get("listingInfo") or {}
            match = item.get("matchPrice") or {}
            ticker = (listing.get("symbol") or listing.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            buy_vol = float(match.get("foreignBuyVolume") or 0)
            buy_val = float(match.get("foreignBuyValue") or 0)
            sell_vol = float(match.get("foreignSellVolume") or 0)
            sell_val = float(match.get("foreignSellValue") or 0)
            rows.append({
                "ticker": ticker,
                "buy_value": buy_val,
                "sell_value": sell_val,
                "net_value": buy_val - sell_val,
                "buy_volume": int(buy_vol),
                "sell_volume": int(sell_vol),
                "net_volume": int(buy_vol - sell_vol),
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
    """vnstock KIS (kbs) source — fallback when VCI is down."""

    def _kbs_history(self, ticker: str, start: str, end: str) -> "pd.DataFrame | None":
        import warnings
        warnings.filterwarnings("ignore")
        from vnstock.api.quote import Quote
        try:
            q = Quote(symbol=ticker, source="kbs")
            df = q.history(start=start, end=end, interval="1D")
            return df if df is not None and not df.empty else None
        except SystemExit:
            raise ValueError(f"KBS rate limited for '{ticker}'")  # FallbackProvider → VCI
        except Exception:
            return None

    def fetch_price(self, ticker: str) -> float:
        from datetime import date, timedelta
        df = self._kbs_history(ticker, str(date.today() - timedelta(days=7)), str(date.today()))
        if df is None:
            raise ValueError(f"No KBS price for '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        from datetime import date, timedelta
        start = str(date.today() - timedelta(days=days + 10))
        df = self._kbs_history(ticker, start, str(date.today()))
        if df is None:
            raise ValueError(f"No KBS history for '{ticker}'")
        df["time"] = df["time"].astype(str).str[:10]
        return df[["time", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)


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
    """Lazy-load VN ticker universe from hose_tickers.json + securities DB (cached)."""
    global _vn_ticker_cache
    if _vn_ticker_cache is not None:
        return _vn_ticker_cache
    tickers: set[str] = set()
    # Try hose_tickers.json first (fast, no DB)
    try:
        import json
        from pathlib import Path as _P
        json_path = _P(__file__).parent.parent / "data" / "hose_tickers.json"
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            tickers.update(item["ticker"].upper() for item in data if item.get("ticker"))
    except Exception:
        pass
    # Supplement with securities table if available
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM securities")
                tickers.update(r[0].upper() for r in cur.fetchall())
    except Exception:
        pass
    _vn_ticker_cache = tickers or {"HPG", "VCB", "FPT", "VNM", "TCB", "MBB", "VHM"}
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
                FallbackProvider(VciDirectProvider(), TcbsDirectProvider()),
            )
    return YFinanceProvider()
