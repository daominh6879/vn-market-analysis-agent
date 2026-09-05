"""
agents/state.py — AgentState TypedDict for bài 22 sequential graph.

Rule: state only holds paths to large data, never DataFrames or tables.
"""

from __future__ import annotations

import time
from typing import TypedDict


class AgentState(TypedDict, total=False):
    ticker: str
    query: str
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
    # Grading (guide A5/A6)
    grades: dict             # {"verdict": "enough" | "insufficient" | "rewrite"}
    iteration: int           # loop counter for rewrite guard
    # RAG-Fusion (rag/rag_fusion_graph.py)
    sub_queries: list[str]   # generated sub-queries (legacy)
    sub_tasks: list[dict]    # structured sub-tasks [{intent, tickers, question}]
    sub_results: list[str]   # raw data gathered per sub-query (no LLM)
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
    original_query: str      # verbatim user message — used for cache key (query may be LLM-expanded)
    # Cache (set by check_cache_node / cache_save_node inside graph)
    _cache_hit: bool
    _cache_tier: str
    _cache_key: dict         # CacheKey.model_dump() or None — serializable, not the Pydantic object
    # Self-critique loop (synthesize_final → critique_report_node)
    critique_pass: bool      # verdict from critique_report_node
    critique_feedback: str   # feedback folded into synthesize retry
    critique_attempts: int   # retry counter, capped by MAX_CRITIQUE
    report_candidates: list[str]  # all synthesize attempts, kept to pick the best on retry exhaustion
    critique_results: list[bool]  # per-attempt pass/fail, aligned with report_candidates
    # Sub-task re-plan guard (run_subqueries_node → decompose_node)
    sub_results_empty_ratio: float  # fraction of sub-tasks returning empty/error data
    replan_attempted: bool          # re-plan already tried once
    replan_note: str                # failure note fed back into decompose
    # Per-turn budget guard (route_after_subqueries / route_after_critique)
    llm_calls: int                  # count of graph LLM calls this turn (router call not included)
    turn_started_at: float          # time.time() at turn start — wall-clock guard


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
        llm_calls=0,
        turn_started_at=time.time(),
    )
