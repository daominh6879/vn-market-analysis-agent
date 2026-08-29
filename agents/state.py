"""
agents/state.py — AgentState TypedDict for bài 22 sequential graph.

Rule: state only holds paths to large data, never DataFrames or tables.
"""

from __future__ import annotations

import re
from typing import TypedDict

_MARKET_INDICES = frozenset({
    "VNINDEX", "VN-INDEX", "VN30", "VN100",
    "HOSE", "HNX", "UPCOM", "HNX30",
})
_MARKET_KEYWORDS = frozenset({"THỊ TRƯỜNG", "MARKET", "TTCK"})
# Vietnamese stopwords that look like tickers but aren't
_VN_NOISE = frozenset({
    "PHÂN", "TÍCH", "HÔM", "NAY", "TUẦN", "TỚI", "NGÀNH",
    "CỔ", "PHIẾU", "CHỈ", "SỐ", "VÀ", "CÁC", "NHỀ",
})


class AgentState(TypedDict, total=False):
    ticker: str
    query: str
    is_market_query: bool
    summary: str
    price_data_path: str     # path to saved OHLCV CSV — never store DataFrame here
    tech_signals: str
    risk_verdict: str        # "OK (volatility=X%)" | "HIGH_VOLATILITY" | "INSUFFICIENT_DATA"
    news_data: str
    sentiment: str
    report: str
    history: list            # [{step, action, result/tokens/elapsed}]
    error: str
    step_count: int
    # Bài 28: conversation context
    conversation_id: str
    messages: list[dict]     # last N turns [{role, content}] — never full history


def detect_query_type(query: str) -> tuple[str, bool]:
    """Return (ticker, is_market_query) from a user query string."""
    upper = query.upper()
    for idx in _MARKET_INDICES:
        if idx in upper:
            return idx, True
    for kw in _MARKET_KEYWORDS:
        if kw in upper:
            return "VNINDEX", True
    for m in re.finditer(r"\b([A-Z]{2,5})\b", upper):
        t = m.group(1)
        if t not in _VN_NOISE:
            return t, False
    words = upper.split()
    return (words[-1].strip(".,!?") if words else "HPG"), False


def make_initial_state(query: str) -> AgentState:
    ticker, is_market = detect_query_type(query)
    return AgentState(
        ticker=ticker,
        query=query,
        is_market_query=is_market,
        step_count=0,
        history=[],
        error="",
    )
