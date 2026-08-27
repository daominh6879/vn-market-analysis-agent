"""
tests/test_phase4.py — Unit tests for Phase 4: calculate_indicators (extended),
detect_candle_pattern, find_support_resistance.

No network calls. Pure computation on synthetic DataFrames.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from tools.price import calculate_indicators, detect_candle_pattern
from tools.levels import find_support_resistance
from tools.result import ToolResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 250, base_close: float = 1_791.0,
                slope: float = 0.5) -> pd.DataFrame:
    """Tạo OHLCV n phiên với giá tăng dần."""
    base = datetime(2024, 1, 2)
    dates = [base + timedelta(days=i) for i in range(n)]
    closes = [base_close + i * slope for i in range(n)]
    return pd.DataFrame({
        "time": dates,
        "open": [c - 2.0 for c in closes],
        "high": [c + 5.0 for c in closes],
        "low": [c - 5.0 for c in closes],
        "close": closes,
        "volume": [10_000_000 + (i % 10) * 500_000 for i in range(n)],
    })


def _make_flat_ohlcv(n: int = 250, close: float = 1_791.0) -> pd.DataFrame:
    """Tạo OHLCV n phiên giá phẳng (để kiểm tra volume ratio)."""
    base = datetime(2024, 1, 2)
    dates = [base + timedelta(days=i) for i in range(n)]
    df = pd.DataFrame({
        "time": dates,
        "open": [close - 1.0] * n,
        "high": [close + 3.0] * n,
        "low": [close - 3.0] * n,
        "close": [close] * n,
        "volume": [10_000_000] * n,
    })
    # Make last bar's volume clearly lower to test ratio
    df.loc[n - 1, "volume"] = 7_600_000  # ~24% below avg
    return df


# ── calculate_indicators (extended) ──────────────────────────────────────────

class TestCalculateIndicatorsExtended:

    def test_returns_tool_result(self):
        df = _make_ohlcv(250)
        result = calculate_indicators(df, currency="VND")
        assert isinstance(result, ToolResult)
        assert result.status == "ok"

    def test_ma200_present_with_250_sessions(self):
        df = _make_ohlcv(250)
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "MA(200)" in result.data

    def test_ma200_insufficient_data(self):
        df = _make_ohlcv(50)
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "MA(200): không đủ dữ liệu" in result.data

    def test_ema200_present_with_250_sessions(self):
        df = _make_ohlcv(250)
        result = calculate_indicators(df)
        assert "EMA(200)" in result.data

    def test_adx_present_with_hlc(self):
        df = _make_ohlcv(250)
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "ADX(14)" in result.data

    def test_adx_missing_without_hlc_columns(self):
        df = _make_ohlcv(250)[["time", "close", "volume"]]
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "ADX(14): thiếu cột high/low" in result.data

    def test_ichimoku_present_with_enough_data(self):
        df = _make_ohlcv(250)
        result = calculate_indicators(df)
        assert result.status == "ok"
        # Either shows Ichimoku result or "không đủ dữ liệu" — but key must be in output
        assert "Ichimoku" in result.data

    def test_ichimoku_insufficient_with_30_sessions(self):
        df = _make_ohlcv(30)
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "Ichimoku: không đủ dữ liệu (cần ít nhất 52 phiên)" in result.data

    def test_volume_ratio_roughly_correct(self):
        df = _make_flat_ohlcv(250, close=1_791.0)
        result = calculate_indicators(df)
        assert result.status == "ok"
        # Last bar volume = 7_600_000, avg of prior 100 bars = 10_000_000
        # ratio ≈ 0.76 → ~24% below
        assert "thấp hơn TB" in result.data
        assert "24%" in result.data

    def test_volume_missing_column_graceful(self):
        df = _make_ohlcv(250)[["time", "open", "high", "low", "close"]]
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "Volume TB: thiếu cột volume" in result.data

    def test_price_above_ma50_when_trending_up(self):
        df = _make_ohlcv(250, base_close=1_000.0, slope=2.0)
        result = calculate_indicators(df)
        assert result.status == "ok"
        assert "trên MA(50)" in result.data

    def test_empty_df_returns_invalid_input(self):
        result = calculate_indicators(pd.DataFrame())
        assert result.status == "invalid_input"

    def test_none_df_returns_invalid_input(self):
        result = calculate_indicators(None)
        assert result.status == "invalid_input"

    def test_missing_close_returns_invalid_input(self):
        df = pd.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5]})
        result = calculate_indicators(df)
        assert result.status == "invalid_input"


# ── detect_candle_pattern ─────────────────────────────────────────────────────

def _single_candle(o: float, h: float, l: float, c: float) -> pd.DataFrame:
    return pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]})


class TestDetectCandlePattern:

    def test_returns_tool_result(self):
        df = _single_candle(100, 110, 90, 105)
        result = detect_candle_pattern(df)
        assert isinstance(result, ToolResult)

    def test_doji(self):
        # body = 1, range = 20 → body_ratio = 0.05 < 0.1
        df = _single_candle(100, 110, 90, 101)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "Doji" in result.data

    def test_bullish_marubozu(self):
        # body = 18, range = 20, tiny shadows
        df = _single_candle(100, 118.5, 99.5, 118)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "Marubozu" in result.data
        assert "xanh" in result.data

    def test_bearish_marubozu(self):
        df = _single_candle(118, 118.5, 99.5, 100)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "Marubozu" in result.data
        assert "đỏ" in result.data

    def test_hammer(self):
        # open=100, close=102, high=103, low=80 → lower_shadow=20, body=2
        df = _single_candle(100, 103, 80, 102)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "Hammer" in result.data

    def test_uses_last_row_of_multirow_df(self):
        rows = [
            {"open": 100, "high": 110, "low": 90, "close": 101},  # Doji
            {"open": 100, "high": 103, "low": 80, "close": 102},  # Hammer
        ]
        df = pd.DataFrame(rows)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "Hammer" in result.data

    def test_empty_df_invalid_input(self):
        result = detect_candle_pattern(pd.DataFrame())
        assert result.status == "invalid_input"

    def test_missing_columns_invalid_input(self):
        df = pd.DataFrame({"close": [100]})
        result = detect_candle_pattern(df)
        assert result.status == "invalid_input"

    def test_zero_range_candle(self):
        # high = low = open = close
        df = _single_candle(100, 100, 100, 100)
        result = detect_candle_pattern(df)
        assert result.status == "ok"
        assert "range = 0" in result.data


# ── find_support_resistance ───────────────────────────────────────────────────

class TestFindSupportResistance:

    def _make_swing_df(self) -> pd.DataFrame:
        """50 phiên với swing rõ ràng: giảm → tăng → giảm → tăng."""
        closes = (
            list(range(100, 80, -1))   # 20 bars down
            + list(range(80, 110, 1))  # 30 bars up
        )
        n = len(closes)
        highs = [c + 3 for c in closes]
        lows = [c - 3 for c in closes]
        return pd.DataFrame({
            "open": [c - 1 for c in closes],
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000] * n,
        })

    def test_returns_tool_result(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        assert isinstance(result, ToolResult)

    def test_ok_with_enough_data(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        assert result.status == "ok"

    def test_data_has_expected_keys(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        assert isinstance(result.data, dict)
        assert "supports" in result.data
        assert "resistances" in result.data
        assert "nearest_round" in result.data
        assert "close" in result.data

    def test_supports_below_close(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        close = result.data["close"]
        for s in result.data["supports"]:
            assert s < close, f"Support {s} >= close {close}"

    def test_resistances_above_close(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        close = result.data["close"]
        for r in result.data["resistances"]:
            assert r > close, f"Resistance {r} <= close {close}"

    def test_nearest_round_close_to_price(self):
        # close ≈ 109, nearest round in [1600..2100 step 50] is far — pass custom
        df = self._make_swing_df()
        result = find_support_resistance(df, round_levels=[100.0, 110.0, 120.0])
        assert result.status == "ok"
        assert result.data["nearest_round"] == 110.0

    def test_insufficient_data_returns_no_data(self):
        df = _make_ohlcv(5)
        result = find_support_resistance(df, window=5)
        assert result.status == "no_data"

    def test_empty_df_invalid_input(self):
        result = find_support_resistance(pd.DataFrame())
        assert result.status == "invalid_input"

    def test_missing_columns_invalid_input(self):
        df = pd.DataFrame({"close": [100, 101]})
        result = find_support_resistance(df)
        assert result.status == "invalid_input"

    def test_max_3_supports_returned(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        assert len(result.data["supports"]) <= 3

    def test_max_3_resistances_returned(self):
        df = self._make_swing_df()
        result = find_support_resistance(df)
        assert len(result.data["resistances"]) <= 3
