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
    # Routing + grading (guide A5/A6)
    route: str               # "knowledge" | "data" — set by route_question
    grades: dict             # {"verdict": "enough" | "insufficient" | "rewrite"}
    iteration: int           # loop counter for rewrite guard
    # RAG-Fusion (rag/rag_fusion_graph.py)
    sub_queries: list[str]   # generated sub-queries
    fused_chunks: list[str]  # RRF-merged top chunks
    sources_used: list[str]  # source labels (BCTC, TIN TỨC, WEB, …)
    # Intent routing (set by classify_node inside graph)
    intent: str              # "price_action" | "technical_analysis" | "rag_qa" | ...
    classify_reason: str     # reason string from RouterResult — e.g. "ticker HPG default"
    # Clarification (set by verify_context node; pending saved to Postgres by verify_context)
    needs_clarification: bool
    clarification_message: str
    pending_context: dict    # PendingContext dict
    # Conversation context (passed in by stream_turn)
    conversation_id: str
    user_id: str
    tenant_id: str           # for cache key namespacing
    messages: list[dict]     # last N turns [{role, content}] — for cache turn-1 check
    # Cache (set by check_cache_node / cache_save_node inside graph)
    _cache_hit: bool
    _cache_tier: str
    _cache_key: object       # CacheKey instance or None


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


def make_initial_state(
    query: str,
    conversation_id: str = "",
    user_id: str = "",
    tenant_id: str = "default",
    messages: list | None = None,
) -> AgentState:
    """Minimal initial state — intent/ticker/cache set by graph nodes."""
    return AgentState(
        query=query,
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        messages=messages or [],
        step_count=0,
        history=[],
        error="",
    )
