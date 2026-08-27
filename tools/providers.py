"""
tools/providers.py — PriceProvider interface + concrete implementations (bài 21).

Providers:
  VciDirectProvider  — VCI REST API, không dùng vnstock
  YFinanceProvider   — yfinance cho mã NYSE/NASDAQ

Không import vnstock ở đây.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
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
                "time":   datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d"),
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
        """Fetch latest OHLCV for multiple tickers in one API call. Returns {ticker: df}."""
        import httpx

        payload = {
            "timeFrame": "ONE_DAY",
            "symbols": [t.upper() for t in tickers],
            "to": int(datetime.now().timestamp()),
            "countBack": count_back,
        }
        resp = httpx.post(self._URL, json=payload, headers=self._HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not data:
            return {}

        upper_tickers = [t.upper() for t in tickers]
        result: dict[str, pd.DataFrame] = {}
        for idx, symbol_data in enumerate(data):
            # VCI may return a "sym" field; fall back to request order if absent
            sym = (symbol_data.get("sym") or symbol_data.get("s") or "").upper()
            if not sym:
                sym = upper_tickers[idx] if idx < len(upper_tickers) else f"UNKNOWN_{idx}"
            if not symbol_data.get("t"):
                continue
            rows = [
                {
                    "time":   datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d"),
                    "open":   symbol_data["o"][i],
                    "high":   symbol_data["h"][i],
                    "low":    symbol_data["l"][i],
                    "close":  symbol_data["c"][i],
                    "volume": symbol_data["v"][i],
                }
                for i, ts in enumerate(symbol_data["t"])
            ]
            df = pd.DataFrame(rows).sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
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
                "time":                   datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d"),
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


def _detect_provider(ticker: str) -> PriceProvider:
    """
    Provider routing:
    - VN broad indices (VNINDEX/VN30/HNX/HNX30/UPCOM) → SsiIndexProvider
    - 2–4 chars, no dot → VciDirectProvider (VN stock tickers)
    - dot or >4 chars → YFinanceProvider (international, USD)
    """
    resolved = resolve_ticker(ticker)
    if resolved in _SSI_DIRECT:
        return SsiIndexProvider()
    if "." not in resolved and len(resolved) <= 4:
        return VciDirectProvider()
    return YFinanceProvider()
