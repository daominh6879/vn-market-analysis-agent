"""
agents/market_brief_state.py — State TypedDict for market_brief_graph.

Rule: state holds only text strings and small dicts, never DataFrames.
"""

from __future__ import annotations

from typing import TypedDict


class MarketBriefState(TypedDict, total=False):
    # ── Input ─────────────────────────────────────────────────────────────
    date: str           # YYYY-MM-DD
    output_path: str    # target .txt file path; None → don't write

    # ── Collected sections (pre-formatted text or "(không có dữ liệu)") ──
    world_block: str        # 🌍 indices bullet list
    gold_oil_block: str     # 💛 gold (world + SJC) + oil
    crypto_block: str       # ₿ coin prices + total mcap
    fx_block: str           # 💵 USD/VND rates

    vn_index_text: str      # "VN-Index đóng cửa X điểm (+Y, +Z%)"
    breadth_text: str       # "N tăng / M giảm"
    movers_text: str        # top value / liquidity leaders
    foreign_text: str       # net buy/sell text
    sector_text: str        # sector performance summary

    news_text: str          # market news headlines
    events_text: str        # upcoming corporate events
    broker_text: str        # CTCK views

    tech_signals: str       # MA, RSI, MACD, ADX, Ichimoku
    candle_pattern: str     # Doji/Marubozu/Hammer/...
    levels_text: str        # support / resistance levels

    # ── LLM output — ONLY this section ────────────────────────────────────
    outlook_text: str       # 🎯 section — LLM writes narrative, not numbers

    # ── Final ─────────────────────────────────────────────────────────────
    report_text: str        # fully rendered report string
    output_file: str        # actual file written

    # ── Tracking ──────────────────────────────────────────────────────────
    missing_fields: list    # list[str] of sections that had no data
    error: str
    history: list
    step_count: int
