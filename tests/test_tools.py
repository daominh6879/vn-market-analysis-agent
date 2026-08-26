"""
tests/test_tools.py — Unit tests cho 3 tool giá chứng khoán (bài 19).

Mock hoàn toàn, không gọi mạng.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tools.price import (
    PriceProvider,
    calculate_indicators,
    get_historical_ohlcv,
    get_realtime_price,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    """Tạo DataFrame OHLCV giả với n phiên."""
    base = datetime(2024, 1, 2)
    dates = [base + timedelta(days=i) for i in range(n)]
    closes = [100_000 + i * 500 for i in range(n)]
    return pd.DataFrame({
        "time": dates,
        "open": [c - 1000 for c in closes],
        "high": [c + 2000 for c in closes],
        "low": [c - 2000 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    })


class MockProvider(PriceProvider):
    def __init__(self, df: pd.DataFrame | None = None, price: float = 80_000.0):
        self._df = df if df is not None else _make_ohlcv()
        self._price = price

    def fetch_price(self, ticker: str) -> float:
        return self._price

    def fetch_history(self, ticker: str, days: int) -> pd.DataFrame:
        return self._df.tail(days).reset_index(drop=True)


# ── get_realtime_price ────────────────────────────────────────────────────────

class TestGetRealtimePrice:
    def test_returns_float(self):
        p = MockProvider(price=95_000.0)
        result = get_realtime_price("FPT", provider=p)
        assert isinstance(result, float)
        assert result == 95_000.0

    def test_ticker_uppercased(self):
        calls = []

        class TrackingProvider(PriceProvider):
            def fetch_price(self, ticker):
                calls.append(ticker)
                return 1.0

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        get_realtime_price("fpt", provider=TrackingProvider())
        assert calls[0] == "FPT"

    def test_empty_ticker_raises(self):
        p = MockProvider()
        with pytest.raises(ValueError, match="ticker"):
            get_realtime_price("", provider=p)

    def test_whitespace_ticker_raises(self):
        p = MockProvider()
        with pytest.raises(ValueError):
            get_realtime_price("   ", provider=p)

    def test_provider_error_propagates(self):
        class BrokenProvider(PriceProvider):
            def fetch_price(self, ticker):
                raise ValueError("upstream down")

            def fetch_history(self, ticker, days):
                return _make_ohlcv()

        with pytest.raises(ValueError, match="upstream down"):
            get_realtime_price("HPG", provider=BrokenProvider())


# ── get_historical_ohlcv ──────────────────────────────────────────────────────

class TestGetHistoricalOhlcv:
    def test_returns_dataframe(self):
        p = MockProvider()
        df = get_historical_ohlcv("VNM", 30, provider=p)
        assert isinstance(df, pd.DataFrame)

    def test_columns_present(self):
        p = MockProvider()
        df = get_historical_ohlcv("VNM", 30, provider=p)
        for col in ["time", "open", "high", "low", "close", "volume"]:
            assert col in df.columns

    def test_days_limit_respected(self):
        p = MockProvider(_make_ohlcv(60))
        df = get_historical_ohlcv("VNM", 30, provider=p)
        assert len(df) <= 30

    def test_no_duplicate_dates(self):
        df_dup = _make_ohlcv(10)
        df_dup = pd.concat([df_dup, df_dup]).reset_index(drop=True)

        class DupProvider(PriceProvider):
            def fetch_price(self, ticker):
                return 1.0

            def fetch_history(self, ticker, days):
                return df_dup.drop_duplicates(subset=["time"]).tail(days).reset_index(drop=True)

        df = get_historical_ohlcv("FPT", 10, provider=DupProvider())
        assert df["time"].duplicated().sum() == 0

    def test_empty_ticker_raises(self):
        p = MockProvider()
        with pytest.raises(ValueError):
            get_historical_ohlcv("", 30, provider=p)

    def test_days_zero_raises(self):
        p = MockProvider()
        with pytest.raises(ValueError):
            get_historical_ohlcv("FPT", 0, provider=p)

    def test_days_negative_raises(self):
        p = MockProvider()
        with pytest.raises(ValueError):
            get_historical_ohlcv("FPT", -5, provider=p)


# ── calculate_indicators ──────────────────────────────────────────────────────

class TestCalculateIndicators:
    def _df(self, n=100):
        return _make_ohlcv(n)

    def test_returns_string(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df())
        assert isinstance(result, str)

    def test_rsi_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "RSI" in result

    def test_macd_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MACD" in result

    def test_ma20_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MA(20)" in result

    def test_ma50_label_present(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "MA(50)" in result

    def test_insufficient_data_no_crash(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(5))
        assert "không đủ dữ liệu" in result

    def test_new_listing_under_14_sessions(self):
        """Mã mới lên sàn dưới 14 phiên — hàm trả thông báo thiếu dữ liệu, không crash."""
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(10))
        assert isinstance(result, str)
        assert "không đủ dữ liệu" in result

    def test_rsi_zone_overbought(self):
        pytest.importorskip("pandas_ta")
        # Tạo chuỗi giá tăng mạnh liên tục → RSI > 70
        closes = [100 + i * 10 for i in range(100)]
        df = pd.DataFrame({
            "time": [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)],
            "open": [c - 1 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1_000_000] * 100,
        })
        result = calculate_indicators(df)
        assert "quá mua" in result

    def test_rsi_zone_oversold(self):
        pytest.importorskip("pandas_ta")
        # Giá giảm mạnh liên tục → RSI < 30
        closes = [1000 - i * 10 for i in range(100)]
        closes = [max(c, 1) for c in closes]
        df = pd.DataFrame({
            "time": [datetime(2024, 1, 1) + timedelta(days=i) for i in range(100)],
            "open": [c + 1 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [max(c - 2, 1) for c in closes],
            "close": closes,
            "volume": [1_000_000] * 100,
        })
        result = calculate_indicators(df)
        assert "quá bán" in result

    def test_empty_df_returns_error_string(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(pd.DataFrame())
        assert "Lỗi" in result or "rỗng" in result

    def test_missing_close_column(self):
        pytest.importorskip("pandas_ta")
        df = pd.DataFrame({"open": [1, 2], "high": [2, 3], "low": [0, 1]})
        result = calculate_indicators(df)
        assert "Lỗi" in result

    def test_no_nan_in_output_for_sufficient_data(self):
        pytest.importorskip("pandas_ta")
        result = calculate_indicators(self._df(100))
        assert "nan" not in result.lower()
        assert "NaN" not in result
