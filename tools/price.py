"""
tools/price.py — Ba tool giá chứng khoán độc lập (bài 19).

Dùng trực tiếp, không cần agent.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


# ── TTL cache ────────────────────────────────────────────────────────────────

_TTL_PRICE = 5 * 60       # 5 min: realtime price — fresh enough for analysis
_TTL_HISTORY = 60 * 60    # 1 hr: daily OHLCV — candle only finalises after close


class _TTLCache:
    """Simple in-memory TTL cache. Thread-unsafe — single-process dev use only."""

    def __init__(self) -> None:
        self._store: dict[Any, tuple[Any, float]] = {}

    def get(self, key: Any) -> Any:
        """Return cached value or _MISS sentinel."""
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


class _Miss:
    """Sentinel for cache miss — avoids ambiguity with None values."""
    def __repr__(self) -> str:
        return "<MISS>"


_MISS = _Miss()
_price_cache = _TTLCache()
_history_cache = _TTLCache()


# ── Interface ────────────────────────────────────────────────────────────────

class PriceProvider(ABC):
    """Swap-able data source. Default implementation: VnstockProvider."""

    @abstractmethod
    def fetch_price(self, ticker: str) -> float:
        """Fetch live price from upstream — called only on cache miss."""
        ...

    @abstractmethod
    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        """Fetch OHLCV history from upstream — called only on cache miss."""
        ...

    def get_price(self, ticker: str) -> float:
        """Return price with 5-min TTL cache."""
        key = (self.__class__.__name__, "price", ticker)
        cached = _price_cache.get(key)
        if not isinstance(cached, _Miss):
            return cached
        value = self.fetch_price(ticker)
        _price_cache.set(key, value, _TTL_PRICE)
        return value

    def get_history(self, ticker: str, days: int) -> pd.DataFrame:
        """Return OHLCV with 1-hour TTL cache."""
        key = (self.__class__.__name__, "history", ticker, days)
        cached = _history_cache.get(key)
        if not isinstance(cached, _Miss):
            return cached
        value = self.fetch_history(ticker, days)
        _history_cache.set(key, value, _TTL_HISTORY)
        return value


class VnstockProvider(PriceProvider):
    """Dùng vnstock v4 (KBS source)."""

    def _quote(self, ticker: str):
        from vnstock import Quote
        return Quote(source="kbs", symbol=ticker.upper(), show_log=False)

    def fetch_price(self, ticker: str) -> float:
        end = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")
        q = self._quote(ticker)
        df = q.history(start=start, end=end, interval="1D")
        if df is None or df.empty:
            raise ValueError(f"Không có dữ liệu giá cho mã '{ticker}'")
        return float(df["close"].iloc[-1])

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        end = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=days + 30)).strftime("%Y-%m-%d")
        q = self._quote(ticker)
        df = q.history(start=start, end=end, interval="1D")
        if df is None or df.empty:
            raise ValueError(f"Không có dữ liệu lịch sử cho mã '{ticker}'")
        df = df.sort_values("time").drop_duplicates(subset=["time"])
        df = df.tail(days).reset_index(drop=True)
        return df[["time", "open", "high", "low", "close", "volume"]]


class YFinanceProvider(PriceProvider):
    """Dùng yfinance cho mã NYSE/NASDAQ (AAPL, TSLA, NVDA...)."""

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


def _detect_provider(ticker: str) -> PriceProvider:
    """Chọn provider dựa vào format ticker.

    2–4 ký tự chữ in hoa, không có dấu chấm → VnstockProvider (VN).
    Có dấu chấm hoặc dài hơn 4 ký tự → YFinanceProvider (quốc tế).
    """
    t = ticker.strip().upper()
    if "." not in t and len(t) <= 4:
        return VnstockProvider()
    return YFinanceProvider()


_default_provider: PriceProvider = VnstockProvider()


def set_provider(provider: PriceProvider) -> None:
    """Swap provider (dùng trong test để inject mock)."""
    global _default_provider
    _default_provider = provider


# ── Tool 1: Giá hiện tại ─────────────────────────────────────────────────────

def get_realtime_price(ticker: str, provider: PriceProvider | None = None) -> float:
    """
    Trả về giá đóng cửa gần nhất (VND).

    Args:
        ticker: Mã CK, ví dụ 'FPT', 'HPG'.
        provider: Override provider (mặc định dùng VnstockProvider).

    Returns:
        Giá float (VND).

    Raises:
        ValueError: ticker rỗng hoặc không có dữ liệu.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    p = provider or _default_provider
    return p.get_price(ticker.upper())


def get_realtime_price_intl(ticker: str, provider: PriceProvider | None = None) -> float:
    """
    Trả về giá đóng cửa gần nhất (USD) cho mã quốc tế (NYSE/NASDAQ).

    Args:
        ticker: Mã CK quốc tế, ví dụ 'AAPL', 'TSLA'.
        provider: Override provider (mặc định dùng YFinanceProvider).

    Returns:
        Giá float (USD).

    Raises:
        ValueError: ticker rỗng hoặc không có dữ liệu.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    p = provider or YFinanceProvider()
    return p.get_price(ticker.upper())


# ── Tool 2: Lịch sử OHLCV ────────────────────────────────────────────────────

def get_historical_ohlcv(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> pd.DataFrame:
    """
    Trả về DataFrame OHLCV `days` phiên gần nhất, không có ngày trùng.

    Columns: time, open, high, low, close, volume.

    Raises:
        ValueError: ticker rỗng, days < 1, hoặc không có dữ liệu.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    if days < 1:
        raise ValueError("days phải >= 1")
    p = provider or _default_provider
    return p.get_history(ticker.upper(), days)


def get_historical_ohlcv_intl(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> pd.DataFrame:
    """
    Trả về DataFrame OHLCV `days` phiên gần nhất cho mã quốc tế (USD).

    Columns: time, open, high, low, close, volume.

    Raises:
        ValueError: ticker rỗng, days < 1, hoặc không có dữ liệu.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    if days < 1:
        raise ValueError("days phải >= 1")
    p = provider or YFinanceProvider()
    return p.get_history(ticker.upper(), days)


# ── Tool 3: Chỉ báo kỹ thuật ─────────────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame, currency: str = "VND") -> str:
    """
    Tính RSI(14), MACD(12,26,9), MA(20), MA(50) và trả text mô tả.

    Không trả số thô — text giúp model hiểu ngữ cảnh.

    Args:
        df: DataFrame từ get_historical_ohlcv (phải có cột 'close').
        currency: Đơn vị tiền tệ ('VND' hoặc 'USD'). Tag vào output.

    Returns:
        Chuỗi mô tả các chỉ báo.
    """
    try:
        import pandas_ta as ta  # noqa: F401
    except ImportError:
        return "Lỗi: pandas-ta chưa cài. Chạy: pip install pandas-ta"

    if df is None or df.empty:
        return "Lỗi: DataFrame rỗng, không thể tính chỉ báo."

    if "close" not in df.columns:
        return "Lỗi: DataFrame thiếu cột 'close'."

    lines: list[str] = [f"[Đơn vị: {currency}]"]

    # RSI(14)
    rsi_series = df.ta.rsi(length=14)
    try:
        rsi = float(rsi_series.iloc[-1]) if rsi_series is not None else float("nan")
    except (TypeError, ValueError):
        rsi = float("nan")
    if pd.isna(rsi):
        lines.append("RSI(14): không đủ dữ liệu (cần ít nhất 14 phiên)")
    else:
        zone = "quá mua" if rsi > 70 else "quá bán" if rsi < 30 else "trung tính"
        lines.append(f"RSI(14) = {rsi:.1f} → vùng {zone}")

    # MACD(12,26,9)
    macd_df = df.ta.macd(fast=12, slow=26, signal=9)
    if macd_df is None or "MACD_12_26_9" not in macd_df.columns:
        lines.append("MACD(12,26,9): không đủ dữ liệu (cần ít nhất 26 phiên)")
    else:
        try:
            macd_val = float(macd_df["MACD_12_26_9"].iloc[-1])
            signal_val = float(macd_df["MACDs_12_26_9"].iloc[-1])
            hist_val = float(macd_df["MACDh_12_26_9"].iloc[-1])
        except (TypeError, ValueError):
            macd_val = float("nan")
            signal_val = float("nan")
            hist_val = float("nan")
        if pd.isna(macd_val):
            lines.append("MACD(12,26,9): không đủ dữ liệu")
        else:
            trend = "tăng" if hist_val > 0 else "giảm"
            lines.append(
                f"MACD(12,26,9) = {macd_val:.2f}, Signal = {signal_val:.2f}, "
                f"Histogram = {hist_val:.2f} → xu hướng {trend}"
            )

    # MA(20)
    ma20 = df.ta.sma(length=20)
    try:
        ma20_val = float(ma20.iloc[-1]) if ma20 is not None else float("nan")
    except (TypeError, ValueError):
        ma20_val = float("nan")
    if pd.isna(ma20_val):
        lines.append("MA(20): không đủ dữ liệu (cần ít nhất 20 phiên)")
    else:
        close_last = float(df["close"].iloc[-1])
        pos = "trên" if close_last > ma20_val else "dưới"
        lines.append(f"MA(20) = {ma20_val:,.0f} → giá đang {pos} MA20")

    # MA(50)
    ma50 = df.ta.sma(length=50)
    try:
        ma50_val = float(ma50.iloc[-1]) if ma50 is not None else float("nan")
    except (TypeError, ValueError):
        ma50_val = float("nan")
    if pd.isna(ma50_val):
        lines.append("MA(50): không đủ dữ liệu (cần ít nhất 50 phiên)")
    else:
        close_last = float(df["close"].iloc[-1])
        pos = "trên" if close_last > ma50_val else "dưới"
        lines.append(f"MA(50) = {ma50_val:,.0f} → giá đang {pos} MA50")

    return "\n".join(lines)


# ── Tool 4: Tin tức tài chính ─────────────────────────────────────────────────

def search_financial_news(
    ticker: str,
    days: int = 7,
    provider: PriceProvider | None = None,  # unused — kept for interface consistency
) -> str:
    """
    Tìm tin tức tài chính về ticker trong N ngày gần nhất từ Qdrant news_chunks.

    Trả text: '[nguồn | ngày] tiêu đề — tóm tắt'.
    Nếu không có tin: chuỗi thông báo, không raise.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    if days < 1 or days > 365:
        raise ValueError("days phải từ 1 đến 365")

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return "Lỗi: không thể import rag.news_index — kiểm tra cài đặt."

    raw = search_news_by_text(ticker.strip().upper(), days=days, limit=10)

    # dedup by URL, keep top 5
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in raw:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)
        if len(unique) == 5:
            break

    if not unique:
        return f"Không có tin tức về {ticker.strip().upper()} trong {days} ngày gần nhất."

    lines: list[str] = []
    for item in unique:
        source = item.get("source", "unknown")
        pub = item.get("published_at", "")
        date_str = pub[:10] if pub else "N/A"
        title = item.get("title", "").strip()
        body = item.get("text", "").strip()
        # first line of body as short summary (skip if it duplicates the title)
        summary_raw = body.split("\n")[0][:120] if body else ""
        summary = summary_raw if not summary_raw.startswith(title) else summary_raw[len(title):].strip(" —-")
        line = f"[{source} | {date_str}] {title}"
        if summary:
            line += f" — {summary}"
        lines.append(line)

    return "\n".join(lines)


# ── Tool 5: Phân tích sentiment thị trường ────────────────────────────────────

def analyze_market_sentiment(ticker: str, days: int = 7) -> str:
    """
    Phân tích cảm xúc thị trường về ticker từ tin tức gần nhất (few-shot LLM).

    Trả: nhãn (tích cực/tiêu cực/trung tính) kèm lý do ngắn gọn.
    Nếu không có tin: chuỗi thông báo, không raise.
    """
    if not ticker or not ticker.strip():
        raise ValueError("ticker không được rỗng")
    if days < 1 or days > 365:
        raise ValueError("days phải từ 1 đến 365")

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return "Lỗi: không thể import rag.news_index — kiểm tra cài đặt."

    raw = search_news_by_text(ticker.strip().upper(), days=days, limit=5)

    # dedup by URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in raw:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    if not unique:
        return f"Không đủ tin tức để phân tích sentiment cho {ticker.strip().upper()}."

    headlines = [item.get("title", "").strip() for item in unique if item.get("title")]
    if not headlines:
        return f"Không đủ tin tức để phân tích sentiment cho {ticker.strip().upper()}."

    # load few-shot examples from data/sentiment_shots_vi.json
    import json
    from pathlib import Path

    shots_path = Path(__file__).parent.parent / "data" / "sentiment_shots_vi.json"
    shots: list[dict] = []
    if shots_path.exists():
        with shots_path.open(encoding="utf-8") as f:
            shots = json.load(f)

    label_vi = {"positive": "tích cực", "negative": "tiêu cực", "neutral": "trung tính"}

    # balanced sample: 2 positive, 2 negative, 1 neutral
    by_label: dict[str, list[str]] = {"positive": [], "negative": [], "neutral": []}
    for s in shots:
        lbl = s.get("label", "neutral")
        if lbl in by_label:
            by_label[lbl].append(s.get("text", ""))

    few_shot_lines: list[str] = []
    for lbl, count in [("positive", 2), ("negative", 2), ("neutral", 1)]:
        for text in by_label[lbl][:count]:
            few_shot_lines.append(f'"{text}" → {label_vi[lbl]}')

    ticker_upper = ticker.strip().upper()
    news_block = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    few_shot_block = "\n".join(few_shot_lines)
    n = len(headlines)

    prompt = (
        f"Tin tức về {ticker_upper} trong {days} ngày gần nhất:\n{news_block}\n\n"
        f"Ví dụ phân loại:\n{few_shot_block}\n\n"
        f"Phân tích xu hướng tổng thể ({n} tin trên) là tích cực, tiêu cực hay trung tính. "
        f"Trả lời đúng format: 'Xu hướng [NHÃN] — [lý do 1–2 câu ngắn gọn]'"
    )

    try:
        from llm.factory import create_client
        from llm.types import Message

        client = create_client()
        resp = client.generate(
            [Message(role="user", content=prompt)],
            max_tokens=150,
            system=(
                "Bạn là chuyên gia phân tích tài chính. "
                "Phân tích sentiment tin tức chứng khoán Việt Nam."
            ),
        )
        return resp.text.strip()
    except Exception as e:
        return f"Lỗi khi phân tích sentiment: {e}"
