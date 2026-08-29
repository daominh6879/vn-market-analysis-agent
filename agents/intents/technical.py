"""
agents/intents/technical.py — Nhóm 2: Phân tích Kỹ thuật.

Collects 6-month OHLCV. Computes: RSI, MACD, MA20/50/200, S/R levels, candle patterns.
LLM synthesizes: trend, cutloss level, reversal warnings.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from llm.factory import create_client
from llm.types import Message
from tools.price import get_historical_ohlcv, calculate_indicators
from tools.result import ToolResult
from agents.intents import strip_preamble, strip_thinking

_CACHE_DIR = Path("outputs/agent_cache")


def _get_ohlcv(ticker: str) -> tuple[str, "pd.DataFrame | None"]:
    """DB-first OHLCV fetch; falls back to live API."""
    try:
        from tools.ohlcv_db import query_ohlcv
        df = query_ohlcv(ticker, days=365)
        if df is not None and len(df) >= 20:
            return "ok", df
    except Exception:
        pass
    r = get_historical_ohlcv(ticker, days=365)
    return r.status, r.data if r.status == "ok" else None


def _compute_obv(df: "pd.DataFrame") -> str:
    if df.empty or "close" not in df.columns or "volume" not in df.columns or len(df) < 10:
        return "N/A"
    direction = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (direction * df["volume"]).cumsum()
    if len(obv) >= 40:
        recent_avg = obv.tail(20).mean()
        prev_avg   = obv.iloc[-40:-20].mean()
        if recent_avg > prev_avg * 1.05:
            trend = "TĂNG → dòng tiền tích lũy"
        elif recent_avg < prev_avg * 0.95:
            trend = "GIẢM → dòng tiền phân phối"
        else:
            trend = "NGANG → trung tính"
        return f"OBV {trend} (hiện tại: {obv.iloc[-1]:,.0f})"
    return f"OBV: {obv.iloc[-1]:,.0f} (chưa đủ 40 phiên để xác định xu hướng)"


def _fibonacci_levels(df: "pd.DataFrame") -> str:
    if df.empty or len(df) < 20:
        return "N/A"
    recent = df.tail(60)
    high = recent["high"].max() if "high" in df.columns else recent["close"].max()
    low  = recent["low"].min()  if "low"  in df.columns else recent["close"].min()
    diff = high - low
    if diff == 0:
        return "N/A"
    lvls = {
        "0.0% (đỉnh)": high,
        "23.6%":        high - 0.236 * diff,
        "38.2%":        high - 0.382 * diff,
        "50.0%":        high - 0.500 * diff,
        "61.8% (vàng)": high - 0.618 * diff,
        "100% (đáy)":   low,
    }
    return "Fibonacci 60 phiên:\n" + "\n".join(f"  {k}: {v:,.0f}" for k, v in lvls.items())


def run(ticker: str, query: str) -> str:
    status, df = _get_ohlcv(ticker)

    tech_signals = "Không có dữ liệu kỹ thuật."
    sr_text      = "Không xác định."
    candle_text  = "Không xác định."
    obv_text     = "N/A"
    fib_text     = "N/A"

    if status == "ok" and df is not None:

        # indicators (RSI, MACD, MA)
        ind_r = calculate_indicators(df)
        tech_signals = ind_r.message

        # support / resistance
        try:
            from tools.levels import find_support_resistance
            sr_r = find_support_resistance(df)
            sr_text = sr_r.message
        except Exception as exc:
            sr_text = f"Không tính được S/R: {exc}"

        obv_text    = _compute_obv(df)
        fib_text    = _fibonacci_levels(df)

        # candle patterns
        try:
            from tools.ohlcv_db import detect_candle_pattern
            cp_r = detect_candle_pattern(df)
            candle_text = cp_r.message
        except Exception:
            try:
                from tools.price import detect_candle_pattern as _cp
                cp_r = _cp(df)
                candle_text = cp_r.message
            except Exception as exc:
                candle_text = f"Không nhận diện được nến: {exc}"

    prompt = f"""Câu hỏi: {query}

Phân tích kỹ thuật {ticker} (dữ liệu 365 phiên):

### Chỉ báo (RSI, MACD, EMA20/SMA50/SMA200)
{tech_signals}

### Hỗ trợ / Kháng cự
{sr_text}

### Dòng tiền OBV
{obv_text}

### Fibonacci Retracement (60 phiên)
{fib_text}

### Mô hình nến
{candle_text}

Logic bắt buộc:
- Cấu trúc xu hướng: EMA20 vs SMA50 vs SMA200 → Short/Mid/Long trend
- RSI > 70 → cảnh báo đu đỉnh; RSI < 30 → vùng quá bán; phân kỳ RSI → tín hiệu đảo chiều
- MACD cắt đường signal lên → tín hiệu mua; xuống → bán
- OBV tăng kèm giá tăng → xác nhận xu hướng; OBV phân kỳ → cảnh báo
- Breakout phải kèm vol > 1.5x MA20 mới xác nhận
- Không được nói "không đủ dữ liệu" — dùng dữ liệu có sẵn để đưa ra nhận định dứt khoát

Viết báo cáo Markdown (không văn bản trước báo cáo):
# Phân tích Kỹ thuật {ticker}
## Cấu trúc Xu hướng (Ngắn / Trung / Dài hạn)
## Động lượng (RSI & MACD)
## Dòng tiền & Khối lượng (OBV, Vol vs MA20)
## Vùng giá quan trọng (Hỗ trợ / Kháng cự / Fibonacci)
## Tín hiệu nến
## Kế hoạch Giao dịch
| | Giá |
|---|---|
| **Entry** | [giá vào lệnh hợp lý] |
| **Stop Loss** | [hỗ trợ gần nhất - 3~5%] |
| **Take Profit 1** | [kháng cự gần nhất] |
| **Take Profit 2** | [kháng cự tiếp theo] |
| **R:R** | [tính = (TP1-Entry)/(Entry-SL) — bắt buộc ≥ 1:2] |
[Nguồn: VCI REST API]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=2500,
        temperature=0,
        system=(
            "Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam. "
            "Xuất NGAY báo cáo Markdown — bắt đầu bằng '# Phân tích Kỹ thuật ...'. "
            "TUYỆT ĐỐI KHÔNG viết suy nghĩ, lý luận, phân vân, hay meta-commentary. "
            "KHÔNG có văn bản trước '#' đầu tiên. "
            "Kế hoạch Giao dịch BẮT BUỘC có Entry / SL / TP1 / TP2 / R:R — "
            "R:R phải ≥ 1:2, nếu không đạt thì ghi 'Setup chưa đủ hấp dẫn'. "
            "Chỉ báo cáo cuối cùng."
        ),
    )
    return strip_thinking(strip_preamble(resp.text.strip()))
