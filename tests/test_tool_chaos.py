"""
tests/test_tool_chaos.py — 5 tình huống lỗi thực tế (bài 20).

Mọi tình huống: không raise, luôn trả ToolResult với status phù hợp.

Scenario 1: ticker không tồn tại        → no_data
Scenario 2: ngày lễ, không có dữ liệu  → no_data
Scenario 3: server HTTP 500             → upstream_error
Scenario 4: timeout                     → upstream_error
Scenario 5: rate limited (429)          → rate_limited
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from tools.price import PriceProvider, get_realtime_price, get_historical_ohlcv
from tools.result import ToolResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    base = datetime(2024, 1, 2)
    return pd.DataFrame({
        "time": [base + timedelta(days=i) for i in range(n)],
        "open": [100_000] * n,
        "high": [102_000] * n,
        "low": [98_000] * n,
        "close": [101_000] * n,
        "volume": [1_000_000] * n,
    })


class _ErrorProvider(PriceProvider):
    """Provider giả lập các loại lỗi."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_price(self, ticker: str) -> float:
        raise self._exc

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        raise self._exc


# ── Scenario 1: ticker không tồn tại ─────────────────────────────────────────

class TestNonExistentTicker:
    def test_price_returns_no_data(self):
        provider = _ErrorProvider(ValueError("Không có dữ liệu giá cho 'FAKEMÃ' — mã không tồn tại"))
        result = get_realtime_price("FAKEMÃ", provider=provider)
        assert isinstance(result, ToolResult), "Phải trả ToolResult, không raise"
        assert result.status == "no_data"
        assert result.data is None

    def test_ohlcv_returns_no_data(self):
        provider = _ErrorProvider(ValueError("Không có dữ liệu lịch sử cho 'FAKEMÃ'"))
        result = get_historical_ohlcv("FAKEMÃ", 30, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "no_data"
        assert result.data is None

    def test_message_is_actionable(self):
        provider = _ErrorProvider(ValueError("mã không tồn tại"))
        result = get_realtime_price("FAKEMÃ", provider=provider)
        # message phải có hướng dẫn — không chỉ là "có lỗi xảy ra"
        assert len(result.message) > 20
        assert result.message != "có lỗi xảy ra"


# ── Scenario 2: ngày lễ, không có dữ liệu giao dịch ─────────────────────────

class TestHolidayNoData:
    def test_price_on_holiday_returns_no_data(self):
        provider = _ErrorProvider(ValueError("Không có dữ liệu giá ngày 2024-01-01 — ngày lễ"))
        result = get_realtime_price("HPG", provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "no_data"

    def test_ohlcv_on_holiday_returns_no_data(self):
        provider = _ErrorProvider(ValueError("Không có phiên giao dịch trong khoảng thời gian yêu cầu"))
        result = get_historical_ohlcv("HPG", 1, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "no_data"
        assert result.data is None

    def test_no_data_message_suggests_alternative(self):
        provider = _ErrorProvider(ValueError("ngày lễ — không có phiên"))
        result = get_historical_ohlcv("HPG", 1, provider=provider)
        assert result.status == "no_data"
        # message hướng dẫn — nên gợi ý thay đổi tham số
        assert len(result.message) > 20


# ── Scenario 3: server HTTP 500 ──────────────────────────────────────────────

class TestServerHttp500:
    def test_price_http500_returns_upstream_error(self):
        provider = _ErrorProvider(ConnectionError("HTTP 500: Internal Server Error"))
        result = get_realtime_price("FPT", provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "upstream_error"
        assert result.data is None

    def test_ohlcv_http500_returns_upstream_error(self):
        provider = _ErrorProvider(ConnectionError("HTTP 500: Internal Server Error"))
        result = get_historical_ohlcv("FPT", 30, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "upstream_error"

    def test_upstream_error_message_says_retry(self):
        provider = _ErrorProvider(ConnectionError("HTTP 500: Internal Server Error"))
        result = get_realtime_price("FPT", provider=provider)
        # message phải hướng dẫn thử lại, không phải "có lỗi"
        msg = result.message.lower()
        assert "thử" in msg or "lại" in msg


# ── Scenario 4: timeout ───────────────────────────────────────────────────────

class TestTimeout:
    def test_price_timeout_returns_upstream_error(self):
        provider = _ErrorProvider(TimeoutError("timed out connecting to upstream"))
        result = get_realtime_price("VNM", provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "upstream_error"
        assert result.data is None

    def test_ohlcv_timeout_returns_upstream_error(self):
        provider = _ErrorProvider(TimeoutError("timed out"))
        result = get_historical_ohlcv("VNM", 60, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "upstream_error"

    def test_timeout_message_says_retry_after_wait(self):
        provider = _ErrorProvider(TimeoutError("timed out"))
        result = get_realtime_price("VNM", provider=provider)
        msg = result.message.lower()
        assert "thử" in msg or "phút" in msg


# ── Scenario 5: rate limited (429) ────────────────────────────────────────────

class TestRateLimited:
    def test_price_rate_limited_returns_rate_limited(self):
        provider = _ErrorProvider(Exception("HTTP 429: Too Many Requests. Rate limit exceeded."))
        result = get_realtime_price("MSN", provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "rate_limited"
        assert result.data is None

    def test_ohlcv_rate_limited_returns_rate_limited(self):
        provider = _ErrorProvider(Exception("429 Too Many Requests. Rate limit exceeded."))
        result = get_historical_ohlcv("MSN", 30, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status == "rate_limited"

    def test_rate_limited_message_warns_not_to_retry_immediately(self):
        provider = _ErrorProvider(Exception("rate limit exceeded"))
        result = get_realtime_price("MSN", provider=provider)
        assert result.status == "rate_limited"
        # message phải cảnh báo không gọi lại ngay
        msg = result.message.lower()
        assert "chờ" in msg or "giây" in msg or "đừng" in msg


# ── Cross-cutting: không tình huống nào raise ────────────────────────────────

class TestNeverRaises:
    """Mọi tình huống đều trả ToolResult — không bao giờ raise ra ngoài."""

    @pytest.mark.parametrize("exc", [
        ValueError("not found"),
        ConnectionError("HTTP 500"),
        TimeoutError("timed out"),
        Exception("429 rate limit"),
        RuntimeError("unexpected"),
    ])
    def test_price_never_raises(self, exc):
        provider = _ErrorProvider(exc)
        result = get_realtime_price("HPG", provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status in {"no_data", "upstream_error", "rate_limited"}

    @pytest.mark.parametrize("exc", [
        ValueError("not found"),
        ConnectionError("HTTP 500"),
        TimeoutError("timed out"),
        Exception("429 rate limit"),
        RuntimeError("unexpected"),
    ])
    def test_ohlcv_never_raises(self, exc):
        provider = _ErrorProvider(exc)
        result = get_historical_ohlcv("HPG", 30, provider=provider)
        assert isinstance(result, ToolResult)
        assert result.status in {"no_data", "upstream_error", "rate_limited"}
