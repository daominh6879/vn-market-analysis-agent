"""
tools/price.py — Tool giá chứng khoán (bài 19 + 19B + 20).

Mọi public function trả ToolResult — không raise ra ngoài,
không trả empty list trần. Agent đọc .message để quyết định bước tiếp.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from tools.result import ToolResult


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


# ── Error mapping helper ──────────────────────────────────────────────────────

def _map_upstream_error(ticker: str, exc: Exception) -> ToolResult:
    """Map generic exception → ToolResult với status phù hợp và message có ích."""
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "too many" in msg:
        return ToolResult(
            status="rate_limited",
            data=None,
            message=(
                f"Đã vượt giới hạn request khi lấy dữ liệu '{ticker}'. "
                "Chờ 60 giây rồi thử lại. Đừng gọi lại ngay — sẽ bị chặn tiếp."
            ),
        )
    if "timeout" in msg or "timed out" in msg:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Timeout khi kết nối nguồn dữ liệu cho '{ticker}'. "
                "Thử lại sau 1–2 phút. Không cần đổi tham số."
            ),
        )
    if "500" in msg or "server error" in msg or "internal server" in msg:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Server nguồn dữ liệu trả lỗi 500 khi lấy '{ticker}'. "
                "Đây là lỗi tạm thời phía server. Thử lại sau 1–2 phút, không đổi tham số."
            ),
        )
    return ToolResult(
        status="upstream_error",
        data=None,
        message=(
            f"Lỗi kết nối khi lấy dữ liệu '{ticker}': {exc}. "
            "Thử lại sau vài phút."
        ),
    )


# ── Tool 1: Giá hiện tại ─────────────────────────────────────────────────────

def get_realtime_price(ticker: str, provider: PriceProvider | None = None) -> ToolResult:
    """Trả giá đóng cửa gần nhất (VND). Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã hợp lệ như 'HPG' hoặc 'FPT'.",
        )
    t = ticker.strip().upper()
    p = provider or _default_provider
    try:
        price = p.get_price(t)
        return ToolResult(
            status="ok",
            data=price,
            message=f"Giá {t}: {price:,.0f} VND (phiên gần nhất).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu giá cho '{t}': {e}. "
                "Kiểm tra mã CK đúng chính tả. Thử mã khác hoặc dùng get_historical_ohlcv."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


def get_realtime_price_intl(ticker: str, provider: PriceProvider | None = None) -> ToolResult:
    """Trả giá đóng cửa gần nhất (USD) cho mã quốc tế. Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã quốc tế như 'AAPL' hoặc 'TSLA'.",
        )
    t = ticker.strip().upper()
    p = provider or YFinanceProvider()
    try:
        price = p.get_price(t)
        return ToolResult(
            status="ok",
            data=price,
            message=f"Giá {t}: {price:.2f} USD (phiên gần nhất).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu giá cho '{t}': {e}. "
                "Kiểm tra mã NYSE/NASDAQ đúng chính tả. Thử mã khác."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


# ── Tool 2: Lịch sử OHLCV ────────────────────────────────────────────────────

def get_historical_ohlcv(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> ToolResult:
    """Trả DataFrame OHLCV `days` phiên gần nhất. Luôn trả ToolResult, không raise."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã hợp lệ như 'HPG'.",
        )
    if days < 1:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải >= 1. Thử days=30 hoặc days=60.",
        )
    t = ticker.strip().upper()
    p = provider or _default_provider
    try:
        df = p.get_history(t, days)
        return ToolResult(
            status="ok",
            data=df,
            message=f"Lấy được {len(df)} phiên lịch sử OHLCV của {t} (VND).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu lịch sử cho '{t}': {e}. "
                "Kiểm tra mã CK hoặc giảm số ngày (days)."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


def get_historical_ohlcv_intl(
    ticker: str,
    days: int = 60,
    provider: PriceProvider | None = None,
) -> ToolResult:
    """Trả DataFrame OHLCV `days` phiên gần nhất cho mã quốc tế (USD). Luôn trả ToolResult."""
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã quốc tế như 'AAPL'.",
        )
    if days < 1:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải >= 1. Thử days=30.",
        )
    t = ticker.strip().upper()
    p = provider or YFinanceProvider()
    try:
        df = p.get_history(t, days)
        return ToolResult(
            status="ok",
            data=df,
            message=f"Lấy được {len(df)} phiên lịch sử OHLCV của {t} (USD).",
        )
    except ValueError as e:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có dữ liệu lịch sử cho '{t}': {e}. "
                "Kiểm tra mã NYSE/NASDAQ hoặc giảm số ngày."
            ),
        )
    except Exception as e:
        return _map_upstream_error(t, e)


# ── Tool 3: Chỉ báo kỹ thuật ─────────────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame, currency: str = "VND") -> ToolResult:
    """
    Tính RSI(14), MACD(12,26,9), MA(20), MA(50). Luôn trả ToolResult, không raise.

    Args:
        df: DataFrame từ get_historical_ohlcv (phải có cột 'close').
        currency: Đơn vị tiền tệ ('VND' hoặc 'USD'). Tag vào output.
    """
    try:
        import pandas_ta as ta  # noqa: F401
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="pandas-ta chưa cài. Không thể tính chỉ báo. Chạy: pip install pandas-ta rồi thử lại.",
        )

    if df is None or df.empty:
        return ToolResult(
            status="invalid_input",
            data=None,
            message="DataFrame rỗng. Truyền vào DataFrame có dữ liệu từ get_historical_ohlcv.",
        )

    if "close" not in df.columns:
        return ToolResult(
            status="invalid_input",
            data=None,
            message="DataFrame thiếu cột 'close'. Truyền vào DataFrame từ get_historical_ohlcv.",
        )

    try:
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

        result_str = "\n".join(lines)
        return ToolResult(status="ok", data=result_str, message=result_str)

    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Lỗi khi tính chỉ báo kỹ thuật: {e}. Kiểm tra DataFrame đầu vào.",
        )


# ── Tool 4: Tin tức tài chính ─────────────────────────────────────────────────

def search_financial_news(
    ticker: str,
    days: int = 7,
    provider: PriceProvider | None = None,  # unused — kept for interface consistency
) -> ToolResult:
    """
    Tìm tin tức tài chính về ticker trong N ngày gần nhất từ Qdrant news_chunks.
    Luôn trả ToolResult, không raise.
    """
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã như 'HPG' hoặc 'VNM'.",
        )
    if days < 1 or days > 365:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải từ 1 đến 365. Thử days=7.",
        )

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="Không thể import rag.news_index. Kiểm tra Qdrant đang chạy và news_chunks đã được index.",
        )

    try:
        raw = search_news_by_text(ticker.strip().upper(), days=days, limit=10)
    except Exception as e:
        return _map_upstream_error(ticker.strip().upper(), e)

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

    t = ticker.strip().upper()
    if not unique:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không có tin tức về {t} trong {days} ngày gần nhất. "
                "Tăng khoảng thời gian (days) hoặc thử mã CK khác."
            ),
        )

    lines: list[str] = []
    for item in unique:
        source = item.get("source", "unknown")
        pub = item.get("published_at", "")
        date_str = pub[:10] if pub else "N/A"
        title = item.get("title", "").strip()
        body = item.get("text", "").strip()
        summary_raw = body.split("\n")[0][:120] if body else ""
        summary = summary_raw if not summary_raw.startswith(title) else summary_raw[len(title):].strip(" —-")
        line = f"[{source} | {date_str}] {title}"
        if summary:
            line += f" — {summary}"
        lines.append(line)

    result_str = "\n".join(lines)
    return ToolResult(
        status="ok",
        data=result_str,
        message=result_str,
    )


# ── Tool 5: Phân tích sentiment thị trường ────────────────────────────────────

def analyze_market_sentiment(ticker: str, days: int = 7) -> ToolResult:
    """
    Phân tích cảm xúc thị trường về ticker từ tin tức gần nhất (few-shot LLM).
    Luôn trả ToolResult, không raise.
    """
    if not ticker or not ticker.strip():
        return ToolResult(
            status="invalid_input",
            data=None,
            message="ticker không được rỗng. Thử với mã như 'HPG' hoặc 'VNM'.",
        )
    if days < 1 or days > 365:
        return ToolResult(
            status="invalid_input",
            data=None,
            message=f"days={days} không hợp lệ. Phải từ 1 đến 365. Thử days=7.",
        )

    try:
        from rag.news_index import search_news_by_text
    except ImportError:
        return ToolResult(
            status="upstream_error",
            data=None,
            message="Không thể import rag.news_index. Kiểm tra Qdrant đang chạy và news_chunks đã được index.",
        )

    try:
        raw = search_news_by_text(ticker.strip().upper(), days=days, limit=5)
    except Exception as e:
        return _map_upstream_error(ticker.strip().upper(), e)

    # dedup by URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in raw:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    t = ticker.strip().upper()
    if not unique:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Không đủ tin tức để phân tích sentiment cho {t}. "
                "Tăng khoảng thời gian (days) hoặc dùng search_financial_news để kiểm tra có tin không."
            ),
        )

    headlines = [item.get("title", "").strip() for item in unique if item.get("title")]
    if not headlines:
        return ToolResult(
            status="no_data",
            data=None,
            message=(
                f"Tin tức về {t} không có tiêu đề. "
                "Không thể phân tích sentiment. Thử lại với khoảng thời gian khác."
            ),
        )

    # load few-shot examples
    import json
    from pathlib import Path

    shots_path = Path(__file__).parent.parent / "data" / "sentiment_shots_vi.json"
    shots: list[dict] = []
    if shots_path.exists():
        with shots_path.open(encoding="utf-8") as f:
            shots = json.load(f)

    label_vi = {"positive": "tích cực", "negative": "tiêu cực", "neutral": "trung tính"}
    by_label: dict[str, list[str]] = {"positive": [], "negative": [], "neutral": []}
    for s in shots:
        lbl = s.get("label", "neutral")
        if lbl in by_label:
            by_label[lbl].append(s.get("text", ""))

    few_shot_lines: list[str] = []
    for lbl, count in [("positive", 2), ("negative", 2), ("neutral", 1)]:
        for text in by_label[lbl][:count]:
            few_shot_lines.append(f'"{text}" → {label_vi[lbl]}')

    news_block = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    few_shot_block = "\n".join(few_shot_lines)
    n = len(headlines)

    prompt = (
        f"Tin tức về {t} trong {days} ngày gần nhất:\n{news_block}\n\n"
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
        result_str = resp.text.strip()
        return ToolResult(status="ok", data=result_str, message=result_str)
    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=(
                f"Lỗi khi gọi LLM để phân tích sentiment cho '{t}': {e}. "
                "Thử lại sau. Nếu lỗi tiếp, kiểm tra kết nối LLM provider."
            ),
        )
