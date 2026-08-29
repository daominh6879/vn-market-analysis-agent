"""
agents/intents/price_action.py — Nhóm 1: Hành động giá & Dòng tiền.

Collects: realtime price, foreign flow, volume vs MA20.
LLM synthesizes: breakout/selloff detection, active buy/sell phe áp đảo.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from llm.factory import create_client
from llm.types import Message
from tools.price import get_realtime_price, get_historical_ohlcv
from tools.result import ToolResult
from agents.intents import strip_preamble


def _get_foreign_flow_summary(ticker: str) -> str:
    """Try DB foreign flow for ticker; fallback to market-level."""
    try:
        from datetime import date
        from tools.foreign_flow_db import query_ticker_foreign_net
        today = date.today()
        row = query_ticker_foreign_net(ticker, today)
        if row:
            return (
                f"Khối ngoại: mua {row['buy_value']:,.0f} tỷ, "
                f"bán {row['sell_value']:,.0f} tỷ, "
                f"ròng {row['net_value']:+,.0f} tỷ"
            )
    except Exception:
        pass
    return "Không có dữ liệu dòng tiền khối ngoại."


def _volume_vs_ma(df: pd.DataFrame, ma_window: int = 20) -> str:
    if df.empty or "volume" not in df.columns or len(df) < ma_window:
        return "Không đủ dữ liệu khối lượng."
    recent_vol = df["volume"].iloc[-1]
    ma_vol = df["volume"].tail(ma_window).mean()
    ratio = recent_vol / ma_vol if ma_vol > 0 else 0
    flag = "⚡ ĐỘT BIẾN" if ratio > 1.5 else ("Bình thường" if ratio >= 0.7 else "Thấp")
    return (
        f"Khối lượng phiên gần nhất: {recent_vol:,.0f} cp "
        f"({ratio:.1%} so với MA{ma_window}) — {flag}"
    )


def _price_change_summary(df: pd.DataFrame) -> str:
    if df.empty or "close" not in df.columns or len(df) < 2:
        return "Không đủ dữ liệu giá."
    last = df["close"].iloc[-1]
    prev = df["close"].iloc[-2]
    change_pct = (last - prev) / prev * 100 if prev > 0 else 0
    direction = "tăng" if change_pct > 0 else ("giảm" if change_pct < 0 else "không đổi")
    alert = ""
    if abs(change_pct) > 3:
        alert = " ⚠️ Biến động mạnh"
    return f"Giá đóng cửa: {last:,.0f} VND ({direction} {abs(change_pct):.2f}%){alert}"


def _load_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Try live API first; fall back to agent cache CSV."""
    r = get_historical_ohlcv(ticker, days=25)
    if r.status == "ok" and r.data is not None:
        return r.data
    # cache written by technical.py / graph.py
    cache = Path("outputs/agent_cache") / f"{ticker}_ohlcv.csv"
    if cache.exists():
        try:
            return pd.read_csv(cache)
        except Exception:
            pass
    return None


def run(ticker: str, query: str) -> str:
    price_r = get_realtime_price(ticker)
    df = _load_ohlcv(ticker)

    price_line = price_r.message

    vol_line = "Không có dữ liệu OHLCV."
    change_line = "Không có dữ liệu OHLCV."
    if df is not None:
        vol_line = _volume_vs_ma(df)
        change_line = _price_change_summary(df)

    flow_line = _get_foreign_flow_summary(ticker)

    prompt = f"""Câu hỏi: {query}

Dữ liệu thị trường {ticker}:
- {price_line}
- {change_line}
- {vol_line}
- {flow_line}

Logic phân tích:
- Nếu giá tăng/giảm >3% VÀ khối lượng >150% MA20 → "Phiên có dòng tiền lớn (Breakout/Selloff)"
- Dòng tiền khối ngoại: nếu ròng dương → tín hiệu tích lũy; âm → phân phối

Viết báo cáo Markdown ngắn gọn (không văn bản trước báo cáo):
# Hành động giá {ticker}
## Giá & Biến động
## Dòng tiền & Khối lượng
## Kết luận ngắn
[Nguồn: VCI REST API / DB]"""

    t0 = time.perf_counter()
    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=1500,
        system="Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam. Trả lời chỉ bằng báo cáo Markdown.",
    )
    return strip_preamble(resp.text.strip())
