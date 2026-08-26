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


# ── Provider selection ────────────────────────────────────────────────────────

# VN market index aliases → VN30 proxy via VCI (VNINDEX unavailable on public APIs).
# VN30 is the best available proxy: top-30 market cap, correlation ~0.99 with VNINDEX.
_VN_INDEX_ALIASES: dict[str, str] = {
    "VNINDEX":  "VN30",
    "VN-INDEX": "VN30",
    "HOSE":     "VN30",
    "VN100":    "VN30",
    "HNX":      "HNX30",  # HNX30 works on VCI; HNX broad index doesn't
    "UPCOM":    "VN30",
}


def resolve_ticker(ticker: str) -> str:
    """Map VN index aliases to tradable VCI symbols. Stock tickers pass through."""
    return _VN_INDEX_ALIASES.get(ticker.strip().upper(), ticker.strip().upper())


def _detect_provider(ticker: str) -> PriceProvider:
    """
    Chọn provider theo format ticker (dùng resolved ticker để detect):
    2–4 ký tự, không dấu chấm → VciDirectProvider (VN stock hoặc VN30/HNX30)
    Có dấu chấm hoặc >4 ký tự  → YFinanceProvider (quốc tế, USD)
    """
    resolved = resolve_ticker(ticker)
    if "." not in resolved and len(resolved) <= 4:
        return VciDirectProvider()
    return YFinanceProvider()
