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
from agents.intents import strip_preamble

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


def run(ticker: str, query: str) -> str:
    status, df = _get_ohlcv(ticker)

    tech_signals = "Không có dữ liệu kỹ thuật."
    sr_text = "Không xác định."
    candle_text = "Không xác định."

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

Phân tích kỹ thuật {ticker} (dữ liệu 180 phiên):

### Chỉ báo (RSI, MACD, MA)
{tech_signals}

### Hỗ trợ / Kháng cự
{sr_text}

### Mô hình nến
{candle_text}

Logic:
- Nếu có MA20/MA50: Giá > MA20 VÀ MA50 → xu hướng tăng ngắn-trung hạn
- Nếu MA không có dữ liệu: dùng S/R levels và RSI để xác định xu hướng
- RSI > 70 → cảnh báo đu đỉnh; RSI < 30 → vùng quá bán
- Cutloss = mốc hỗ trợ gần nhất - 3~5%
- MACD phân kỳ âm → cảnh báo đảo chiều
- Không được nói "không đủ dữ liệu" trong báo cáo — dùng dữ liệu có sẵn để đưa ra nhận định

Viết báo cáo Markdown (không văn bản trước báo cáo):
# Phân tích kỹ thuật {ticker}
## Xu hướng
## Chỉ báo RSI & MACD
## Hỗ trợ / Kháng cự & Mức Cutloss
## Tín hiệu nến
## Khuyến nghị
[Nguồn: VCI REST API]"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=2500,
        system=(
            "Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam. "
            "Trả lời NGAY bằng báo cáo Markdown — KHÔNG có câu giới thiệu, "
            "KHÔNG có văn bản trước dấu '#' đầu tiên, KHÔNG giải thích cách làm."
        ),
    )
    return strip_preamble(resp.text.strip())
