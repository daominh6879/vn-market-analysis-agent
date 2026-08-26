"""
tools/price.py — Ba tool giá chứng khoán độc lập (bài 19).

Dùng trực tiếp, không cần agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

import pandas as pd


# ── Interface ────────────────────────────────────────────────────────────────

class PriceProvider(ABC):
    """Swap-able data source. Default implementation: VnstockProvider."""

    @abstractmethod
    def fetch_price(self, ticker: str) -> float:
        ...

    @abstractmethod
    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        ...


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
    return p.fetch_price(ticker.upper())


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
    return p.fetch_history(ticker.upper(), days)


# ── Tool 3: Chỉ báo kỹ thuật ─────────────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> str:
    """
    Tính RSI(14), MACD(12,26,9), MA(20), MA(50) và trả text mô tả.

    Không trả số thô — text giúp model hiểu ngữ cảnh.

    Args:
        df: DataFrame từ get_historical_ohlcv (phải có cột 'close').

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

    lines: list[str] = []

    # RSI(14)
    rsi_series = df.ta.rsi(length=14)
    rsi = rsi_series.iloc[-1] if rsi_series is not None else float("nan")
    if pd.isna(rsi):
        lines.append("RSI(14): không đủ dữ liệu (cần ít nhất 14 phiên)")
    else:
        zone = "quá mua" if rsi > 70 else "quá bán" if rsi < 30 else "trung tính"
        lines.append(f"RSI(14) = {rsi:.1f} → vùng {zone}")

    # MACD(12,26,9)
    macd_df = df.ta.macd(fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        lines.append("MACD(12,26,9): không đủ dữ liệu (cần ít nhất 26 phiên)")
    else:
        macd_val = macd_df["MACD_12_26_9"].iloc[-1]
        signal_val = macd_df["MACDs_12_26_9"].iloc[-1]
        hist_val = macd_df["MACDh_12_26_9"].iloc[-1]
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
    if ma20 is None or pd.isna(ma20.iloc[-1]):
        lines.append("MA(20): không đủ dữ liệu (cần ít nhất 20 phiên)")
    else:
        close_last = df["close"].iloc[-1]
        pos = "trên" if close_last > ma20.iloc[-1] else "dưới"
        lines.append(f"MA(20) = {ma20.iloc[-1]:,.0f} → giá đang {pos} MA20")

    # MA(50)
    ma50 = df.ta.sma(length=50)
    if ma50 is None or pd.isna(ma50.iloc[-1]):
        lines.append("MA(50): không đủ dữ liệu (cần ít nhất 50 phiên)")
    else:
        close_last = df["close"].iloc[-1]
        pos = "trên" if close_last > ma50.iloc[-1] else "dưới"
        lines.append(f"MA(50) = {ma50.iloc[-1]:,.0f} → giá đang {pos} MA50")

    return "\n".join(lines)
