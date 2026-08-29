"""
tools/levels.py — Tìm vùng hỗ trợ / kháng cự (Phase 4).

find_support_resistance(df): swing high/low + mốc tâm lý tròn.
"""

from __future__ import annotations

import pandas as pd

from tools.result import ToolResult


def find_support_resistance(
    df: pd.DataFrame,
    window: int = 5,
    round_levels: list[float] | None = None,
) -> ToolResult:
    """
    Tìm vùng hỗ trợ/kháng cự từ swing high/low và mốc tâm lý.

    Args:
        df: DataFrame OHLCV với cột high, low, close.
        window: số nến mỗi phía để xác định swing point.
        round_levels: list mốc tâm lý để kiểm tra gần nhất (VND/điểm index).
            Mặc định dùng danh sách điểm index VN-Index tầm 1.700-2.000.
    Returns:
        ToolResult(data=dict) với keys:
            supports: list[float] — vùng hỗ trợ gần nhất (ascending)
            resistances: list[float] — vùng kháng cự gần nhất (ascending)
            nearest_round: float | None — mốc tâm lý gần giá hiện tại nhất
    """
    required = {"high", "low", "close"}
    if df is None or df.empty:
        return ToolResult(status="invalid_input", data=None, message="DataFrame rỗng.")
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        return ToolResult(status="invalid_input", data=None,
                          message=f"Thiếu cột: {missing}.")

    if len(df) < window * 2 + 1:
        return ToolResult(
            status="no_data", data=None,
            message=f"Cần ít nhất {window * 2 + 1} phiên, hiện có {len(df)}.",
        )

    try:
        highs = df["high"].astype(float).reset_index(drop=True)
        lows = df["low"].astype(float).reset_index(drop=True)
        close_last = float(df["close"].iloc[-1])

        swing_highs: list[float] = []
        swing_lows: list[float] = []

        for i in range(window, len(df) - window):
            h = highs[i]
            l = lows[i]
            if h == highs[i - window: i + window + 1].max():
                swing_highs.append(h)
            if l == lows[i - window: i + window + 1].min():
                swing_lows.append(l)

        # Lọc: hỗ trợ = swing low dưới giá, kháng cự = swing high trên giá
        supports = sorted({round(v, 0) for v in swing_lows if v < close_last}, reverse=True)[:3]
        resistances = sorted({round(v, 0) for v in swing_highs if v > close_last})[:3]

        # Mốc tâm lý gần nhất — generated dynamically around actual price
        if round_levels is None:
            # Determine step size based on price magnitude
            if close_last >= 10_000:
                step = 1_000   # stock in VND: 1,000 VND steps (e.g. 21,000 22,000)
            elif close_last >= 1_000:
                step = 100
            else:
                step = 50      # VN-Index style
            lo = max(step, int(close_last * 0.7 // step * step))
            hi = int(close_last * 1.3 // step * step) + step
            round_levels = [float(x) for x in range(lo, hi + step, step)]
        nearest_round: float | None = None
        if round_levels:
            nearest_round = min(round_levels, key=lambda x: abs(x - close_last))

        data = {
            "supports": supports,
            "resistances": resistances,
            "nearest_round": nearest_round,
            "close": close_last,
        }

        lines = [f"Giá hiện tại: {close_last:,.0f}"]
        if supports:
            lines.append(f"Hỗ trợ: {', '.join(f'{v:,.0f}' for v in supports)}")
        else:
            lines.append("Hỗ trợ: không tìm được swing low dưới giá hiện tại")
        if resistances:
            lines.append(f"Kháng cự: {', '.join(f'{v:,.0f}' for v in resistances)}")
        else:
            lines.append("Kháng cự: không tìm được swing high trên giá hiện tại")
        if nearest_round is not None:
            lines.append(f"Mốc tâm lý gần nhất: {nearest_round:,.0f}")

        msg = "\n".join(lines)
        return ToolResult(status="ok", data=data, message=msg)

    except Exception as e:
        return ToolResult(status="upstream_error", data=None,
                          message=f"Lỗi tính hỗ trợ/kháng cự: {e}.")
