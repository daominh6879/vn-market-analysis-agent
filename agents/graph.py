"""
agents/graph.py — LangGraph graph: single entry from raw query → report.

Flow:
  merge_pending_node → classify_node
    └── "conversation" → END  (stream_turn handles LLM streaming)
    └── check_cache_node
          ├── "hit"  → END  (cached report in state["report"])
          └── "miss" → verify_context
                ├── "clarify"  → END  (clarification_message in state; pending saved to Postgres)
                └── "proceed"  → route_question
                      ├── intent nodes (8)  → cache_save_node → END
                      ├── "knowledge" → fusion_search → grade_or_critique
                      │                                    ├── enough      → synthesize → cache_save_node → END
                      │                                    ├── insufficient → run_web_search → synthesize → cache_save_node → END
                      │                                    └── rewrite     → fusion_search (≤ MAX_ITER)
                      └── "data"     → collect → analyze_technical → assess_risk → synthesize → cache_save_node → END

Design rules:
- state stores only paths, never DataFrames
- route_question / grade_or_critique: pure logic, no LLM
- risk node: pure if/else, no model call
- synthesize: LLM via create_client() factory
- classify_node owns Langfuse trace setup (was in _dispatch_intent)
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from langgraph.graph import END, StateGraph

from agents.state import AgentState
from tools.price import (
    analyze_market_sentiment,
    calculate_indicators,
    get_historical_ohlcv,
    search_financial_news,
)

_CACHE_DIR = Path("outputs/agent_cache")
_VOLATILITY_THRESHOLD = 0.04  # 4% daily return std → HIGH_VOLATILITY
_RAG_COLLECTION = os.environ.get("RAG_COLLECTION", "bctc_structural")
_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
MAX_ITER = 2  # max RAG rewrite loops before fallback to web_search

# Lazy BM25 cache — avoid re-loading on every run
_bm25_cache: dict[str, object] = {}

# Intents handled by dedicated nodes — dispatched directly from pick_branch
_INTENT_NODES = frozenset({
    "price_action", "technical_analysis", "rag_qa",
    "news_sentiment", "macro_sector", "investment_case", "screening",
    "market_brief", "breakout_scan",
})

# Keywords that signal a document/financial-report query → "knowledge" path
_KNOWLEDGE_KEYWORDS = frozenset({
    "bctc", "báo cáo tài chính", "p/e", "pe", "roe", "roa", "eps",
    "doanh thu", "lợi nhuận", "tổng tài sản", "vốn chủ sở hữu",
    "biên lợi nhuận", "định giá", "nợ vay", "ebitda",
    "quý 1", "quý 2", "quý 3", "quý 4", "năm tài chính",
    "tài chính", "kiểm toán", "hợp nhất",
})


# ── Node −1: classify_node ────────────────────────────────────────────────────

_MARKET_TICKERS = frozenset({"VNINDEX", "VN-INDEX", "VN30", "VN100", "HOSE", "HNX30"})


def classify_node(state: AgentState) -> dict:
    """Classify intent + extract ticker. Sets up Langfuse trace (was _dispatch_intent)."""
    import uuid
    conv_id = state.get("conversation_id", "")
    try:
        from tracing import current_request_id
        rid = f"{conv_id[:8]}-{uuid.uuid4().hex[:6]}"
        current_request_id.set(rid)
    except Exception:
        rid = uuid.uuid4().hex[:12]

    from agents.classifier import classify_hybrid
    result = classify_hybrid(
        state.get("query", ""),
        messages=state.get("messages"),
    )

    is_market = result.intent == "market_brief" or (result.ticker or "") in _MARKET_TICKERS

    try:
        from langfuse import get_client, observe  # noqa: F401
        get_client().update_current_trace(
            session_id=conv_id,
            user_id=state.get("user_id"),
            input=state.get("query"),
            metadata={"intent": result.intent, "ticker": result.ticker, "request_id": rid},
        )
    except Exception:
        pass

    return {
        "intent": result.intent,
        "ticker": result.ticker or "",
        "is_market_query": is_market,
        "classify_reason": result.reason,
    }


def check_conversation(state: AgentState) -> str:
    """Skip cache/clarify for pure conversation turns — stream_turn handles streaming."""
    return "skip" if state.get("intent") == "conversation" else "verify"


# ── Node 0: check_cache_node ─────────────────────────────────────────────────

def check_cache_node(state: AgentState) -> dict:
    """Check cache using classified intent+ticker. Returns early if hit."""
    from core.cache import make_cache_key, cache_get
    ck = make_cache_key(
        state.get("tenant_id", "default"),
        state.get("query", ""),
        state.get("ticker") or "",
        state.get("intent", "conversation"),
        state.get("messages") or [],
    )
    if ck is None:
        return {"_cache_key": None}
    hit, tier = cache_get(ck)
    if hit:
        return {"report": hit, "_cache_hit": True, "_cache_tier": tier, "_cache_key": ck}
    return {"_cache_key": ck}


def check_cache_hit(state: AgentState) -> str:
    return "hit" if state.get("_cache_hit") else "miss"


# ── Node 0a: clarify_node ─────────────────────────────────────────────────────

def clarify_node(state: AgentState) -> dict:
    """Ask user for missing intent/ticker via interrupt(). On resume, re-classify merged query."""
    from langgraph.types import interrupt
    from memory.clarification import detect_ambiguity, build_clarification_message, merge_with_pending

    class _Route:
        def __init__(self, intent, ticker, reason=""):
            self.intent = intent
            self.ticker = ticker
            self.reason = reason

    route = _Route(
        state.get("intent", "conversation"),
        state.get("ticker"),
        state.get("classify_reason", ""),
    )
    pending = detect_ambiguity(route, state.get("query", ""))
    if pending is None:
        return {}

    question = build_clarification_message(pending)
    answer = interrupt(question)  # pauses graph; resumes when user replies

    merged_query = merge_with_pending(pending, answer)
    from agents.classifier import classify_hybrid
    result = classify_hybrid(merged_query, messages=state.get("messages"))

    return {
        "query": merged_query,
        "intent": result.intent,
        "ticker": result.ticker or "",
        "classify_reason": result.reason,
    }


# ── Node 0a: route_question (guide A5) ───────────────────────────────────────

def route_question(state: AgentState) -> dict:
    """Reset iteration counter. Routing handled by pick_branch conditional edge."""
    return {"iteration": 0}


# ── Conditional edge functions (guide A6) ────────────────────────────────────

def pick_branch(state: AgentState) -> str:
    """Dispatch by intent first; fall back to keyword-based knowledge/data routing."""
    intent = state.get("intent", "")
    if intent in _INTENT_NODES:
        return intent
    if state.get("is_market_query", False):
        return "data"
    if any(kw in state.get("query", "").lower() for kw in _KNOWLEDGE_KEYWORDS):
        return "knowledge"
    return "data"


def decide_next(state: AgentState) -> str:
    """Route after grade_or_critique based on verdict."""
    v = state.get("grades", {}).get("verdict", "enough")
    if v == "enough":
        return "synthesize"
    if state.get("iteration", 0) >= MAX_ITER or v == "insufficient":
        return "web_search"
    return "fusion_search"  # "rewrite" — retry with same query


# ── Node 0b: fusion_search (RAG-Fusion) ──────────────────────────────────────

def _get_bm25(collection: str):
    if collection not in _bm25_cache:
        from rag.retrieval_bm25 import BM25Retriever
        _bm25_cache[collection] = BM25Retriever(collection=collection, use_vn_tokenize=True)
    return _bm25_cache[collection]


def fusion_search(state: AgentState) -> dict:
    """RAG-Fusion: decompose query → multi-retrieve → RRF fuse → fused_chunks."""
    from rag.rag_fusion_graph import run_rag_fusion

    try:
        bm25 = _get_bm25(_RAG_COLLECTION)
        result = run_rag_fusion(
            query=state["query"],
            collection=_RAG_COLLECTION,
            embed_model=_EMBED_MODEL,
            bm25_retriever=bm25,
            ticker=state.get("ticker", "HPG"),
            n_sub_queries=4,
        )
        return {
            "sub_queries": result.get("sub_queries", []),
            "fused_chunks": result.get("fused_chunks", []),
            "sources_used": result.get("sources_used", []),
        }
    except Exception as exc:
        # RAG unavailable — continue without fused context
        return {"sub_queries": [], "fused_chunks": [], "sources_used": []}


# ── Node 0c: grade_or_critique (guide A5) ────────────────────────────────────

def grade_or_critique(state: AgentState) -> dict:
    """Evaluate fused_chunks sufficiency. No LLM call."""
    fused = state.get("fused_chunks", [])
    iteration = state.get("iteration", 0)
    if len(fused) >= 3:
        return {"grades": {"verdict": "enough"}}
    if len(fused) == 0 and iteration < MAX_ITER:
        return {"grades": {"verdict": "rewrite"}, "iteration": iteration + 1}
    return {"grades": {"verdict": "insufficient"}}


# ── Node 0d: run_web_search (guide A4 tool 3) ─────────────────────────────────

def run_web_search(state: AgentState) -> dict:
    """Web/news fallback when RAG context insufficient."""
    result = search_financial_news(state.get("ticker", ""), days=7)
    return {"news_data": result.message if result.status == "ok" else ""}


# ── Node 1: collect ────────────────────────────────────────────────────────────

def collect(state: AgentState) -> dict:
    ticker = state["ticker"]
    is_market = state.get("is_market_query", False)
    ohlcv_days = 60 if is_market else 60
    news_days = 1 if is_market else 7

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_ohlcv = ex.submit(get_historical_ohlcv, ticker, ohlcv_days)
        fut_news = ex.submit(search_financial_news, ticker, news_days)
        ohlcv_result = fut_ohlcv.result()
        news_result = fut_news.result()

    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if ohlcv_result.status == "ok":
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = str(_CACHE_DIR / f"{ticker}_ohlcv.csv")
        ohlcv_result.data.to_csv(path, index=False)
        updates["price_data_path"] = path
    else:
        updates["error"] = ohlcv_result.message

    updates["news_data"] = (
        news_result.message if news_result.status == "ok"
        else f"[Không có tin tức: {news_result.message}]"
    )
    updates["history"] = state.get("history", []) + [
        {"step": "collect", "ohlcv_status": ohlcv_result.status, "news_status": news_result.status}
    ]
    return updates


# ── Node 2: analyze_technical ─────────────────────────────────────────────────

def analyze_technical(state: AgentState) -> dict:
    path = state.get("price_data_path", "")
    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if not path or not Path(path).exists():
        updates["tech_signals"] = "Không có dữ liệu giá để tính chỉ báo kỹ thuật."
        return updates

    df = pd.read_csv(path)
    result = calculate_indicators(df)
    updates["tech_signals"] = result.message
    return updates


# ── Node 3: assess_risk ────────────────────────────────────────────────────────

def assess_risk(state: AgentState) -> dict:
    """Pure if/else — no model call."""
    path = state.get("price_data_path", "")
    updates: dict = {"step_count": state.get("step_count", 0) + 1}

    if not path or not Path(path).exists():
        updates["risk_verdict"] = "INSUFFICIENT_DATA"
        return updates

    df = pd.read_csv(path)
    if len(df) < 14:
        updates["risk_verdict"] = "INSUFFICIENT_DATA"
        return updates

    returns = df["close"].tail(14).pct_change().dropna()
    volatility = float(returns.std())

    if volatility > _VOLATILITY_THRESHOLD:
        updates["risk_verdict"] = f"HIGH_VOLATILITY (14-session std={volatility:.2%})"
    else:
        updates["risk_verdict"] = f"OK (14-session std={volatility:.2%})"
        sentiment_result = analyze_market_sentiment(state["ticker"], days=7)
        updates["sentiment"] = (
            sentiment_result.message if sentiment_result.status == "ok" else ""
        )

    return updates


# ── Node 4: synthesize ────────────────────────────────────────────────────────

def synthesize(state: AgentState) -> dict:
    from llm.factory import create_client
    from llm.types import Message

    ticker = state.get("ticker", "")
    is_market = state.get("is_market_query", False)
    subject = "thị trường chứng khoán" if is_market else f"cổ phiếu {ticker}"
    tech = state.get("tech_signals") or "Không có dữ liệu kỹ thuật."
    risk = state.get("risk_verdict") or "Chưa đánh giá."
    news = state.get("news_data") or "Không có tin tức."
    sentiment = state.get("sentiment") or ""
    data_source = "yfinance (^VN30 proxy)" if is_market else "VCI REST API"

    high_vol_warning = (
        "\n⚠️ **CẢNH BÁO:** Biến động cao trong 14 phiên gần nhất. Rủi ro tăng đáng kể.\n"
        if "HIGH_VOLATILITY" in risk else ""
    )

    sentiment_block = f"\n## Sentiment thị trường:\n{sentiment}" if sentiment else ""

    fused_chunks = state.get("fused_chunks", [])
    sources_used = state.get("sources_used", [])
    rag_block = ""
    if fused_chunks:
        rag_context = "\n\n---\n\n".join(fused_chunks[:5])
        sources_label = ", ".join(sources_used) if sources_used else "RAG corpus"
        rag_block = f"\n\n### Tài liệu tham khảo (RAG-Fusion — nguồn: {sources_label})\n{rag_context}"

    prompt = f"""Dữ liệu phân tích {subject}:

{high_vol_warning}
### Chỉ báo kỹ thuật
{tech}

### Rủi ro
{risk}

### Tin tức
{news}{sentiment_block}{rag_block}

Viết ngay báo cáo Markdown (không có văn bản nào trước báo cáo). Cấu trúc:
# Báo cáo phân tích {ticker}
## Kết luận: [Tích cực / Trung tính / Tiêu cực]
## Kỹ thuật
## Rủi ro
## Tin tức & Sentiment
## Khuyến nghị

Trích nguồn dạng [Nguồn: {data_source}] hoặc [Nguồn: CafeF/Tavily, <ngày>]."""

    t0 = time.perf_counter()
    client = create_client()
    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=4000,
        system=(
            "Bạn là chuyên gia phân tích tài chính Việt Nam. "
            "Trả lời chỉ bằng báo cáo Markdown, không có văn bản nào trước hoặc sau."
        ),
    )
    elapsed = time.perf_counter() - t0

    return {
        "report": resp.text.strip(),
        "summary": resp.text.strip()[:120],
        "step_count": state.get("step_count", 0) + 1,
        "history": state.get("history", []) + [{
            "step": "synthesize",
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "elapsed_seconds": round(elapsed, 2),
        }],
    }


# ── Intent nodes (thin wrappers — call agents/intents/*.run()) ────────────────

def node_price_action(state: AgentState) -> dict:
    from agents.intents.price_action import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_technical(state: AgentState) -> dict:
    from agents.intents.technical import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_news_sentiment(state: AgentState) -> dict:
    from agents.intents.news_sentiment import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_macro_sector(state: AgentState) -> dict:
    from agents.intents.macro_sector import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_investment_case(state: AgentState) -> dict:
    from agents.intents.investment_case import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_screening(state: AgentState) -> dict:
    from agents.intents.screening import run
    return {"report": run(state.get("ticker"), state.get("query", ""))}


def node_market_brief(state: AgentState) -> dict:
    from datetime import date
    from agents.market_brief_graph import build_brief_graph, make_initial_state as mb_init
    app = build_brief_graph()
    initial = mb_init(date=str(date.today()), output_path="")
    final = app.invoke(initial)
    return {"report": final.get("report_text") or "[Không có báo cáo thị trường]"}


def node_breakout_scan(state: AgentState) -> dict:
    from agents.intents.breakout import run
    return {"report": run(state.get("ticker", ""), state.get("query", ""))}


def node_rag_qa(state: AgentState) -> dict:
    ticker = state.get("ticker")
    query = state.get("query", "")
    if ticker:
        from agents.intents.fundamentals import run as fund_run, _is_sector_comparison
        if _is_sector_comparison(query):
            return {"report": fund_run(ticker, query)}
    from rag.qa import answer as qa_answer
    return {"report": qa_answer(query, ticker=ticker)}


# ── cache_save_node ───────────────────────────────────────────────────────────

def cache_save_node(state: AgentState) -> dict:
    """Persist report to cache after successful generation."""
    ck = state.get("_cache_key")
    report = state.get("report") or ""
    if ck and report and not state.get("_cache_hit"):
        from core.cache import cache_set
        cache_set(ck, report)
    return {}


_INTENT_NODE_NAMES = (
    "node_price_action", "node_technical", "node_news_sentiment",
    "node_macro_sector", "node_investment_case", "node_screening",
    "node_rag_qa", "node_market_brief", "node_breakout_scan",
)

_INTENT_NODE_MAP = {
    "price_action":       "node_price_action",
    "technical_analysis": "node_technical",
    "news_sentiment":     "node_news_sentiment",
    "macro_sector":       "node_macro_sector",
    "investment_case":    "node_investment_case",
    "screening":          "node_screening",
    "rag_qa":             "node_rag_qa",
    "market_brief":       "node_market_brief",
    "breakout_scan":      "node_breakout_scan",
}

# ── Graph builder ─────────────────────────────────────────────────────────────

def _request_approval(state: AgentState) -> dict:
    """Pause for human review. interrupt() suspends graph until resumed via Command(resume=...)."""
    from langgraph.types import interrupt
    proposal = {
        "ticker": state.get("ticker"),
        "risk_verdict": state.get("risk_verdict", "N/A"),
        "tech_signals": (state.get("tech_signals") or "")[:500],
        "news_preview": (state.get("news_data") or "")[:300],
    }
    decision = interrupt(proposal)
    if decision is False:
        return {"error": "rejected_by_user"}
    return {}


def _decide_next_approval(state: AgentState) -> str:
    """decide_next variant that routes to request_approval instead of synthesize."""
    v = state.get("grades", {}).get("verdict", "enough")
    if v == "enough":
        return "request_approval"
    if state.get("iteration", 0) >= MAX_ITER or v == "insufficient":
        return "web_search"
    return "fusion_search"


def build_graph(checkpointer=None, human_approval: bool = False) -> "CompiledGraph":
    """Build the agent graph.

    Args:
        checkpointer: LangGraph checkpointer. Required for clarify_node (interrupt) and
                      human_approval. Pass PostgresCheckpointer() for production use.
        human_approval: When True, inserts request_approval before synthesize so
                        a human can review/reject before the report is written.
                        Used by api/sessions.py (Bài 27).
    """
    g = StateGraph(AgentState)

    g.add_node("classify_node", classify_node)
    g.add_node("check_cache_node", check_cache_node)
    g.add_node("clarify_node", clarify_node)
    g.add_node("route_question", route_question)

    g.add_node("node_price_action", node_price_action)
    g.add_node("node_technical", node_technical)
    g.add_node("node_news_sentiment", node_news_sentiment)
    g.add_node("node_macro_sector", node_macro_sector)
    g.add_node("node_investment_case", node_investment_case)
    g.add_node("node_screening", node_screening)
    g.add_node("node_rag_qa", node_rag_qa)
    g.add_node("node_market_brief", node_market_brief)
    g.add_node("node_breakout_scan", node_breakout_scan)

    g.add_node("fusion_search", fusion_search)
    g.add_node("grade_or_critique", grade_or_critique)
    g.add_node("run_web_search", run_web_search)
    g.add_node("collect", collect)
    g.add_node("analyze_technical", analyze_technical)
    g.add_node("assess_risk", assess_risk)
    g.add_node("synthesize", synthesize)
    g.add_node("cache_save_node", cache_save_node)

    if human_approval:
        g.add_node("request_approval", _request_approval)

    g.set_entry_point("classify_node")
    g.add_conditional_edges("classify_node", check_conversation,
        {"skip": END, "verify": "check_cache_node"})
    g.add_conditional_edges("check_cache_node", check_cache_hit,
        {"hit": END, "miss": "clarify_node"})
    g.add_edge("clarify_node", "route_question")
    g.add_conditional_edges("route_question", pick_branch, {
        **_INTENT_NODE_MAP,
        "knowledge": "fusion_search",
        "data":      "collect",
    })

    if human_approval:
        for n in _INTENT_NODE_NAMES:
            g.add_edge(n, "request_approval")
        g.add_edge("fusion_search", "grade_or_critique")
        g.add_conditional_edges("grade_or_critique", _decide_next_approval,
            {"request_approval": "request_approval", "web_search": "run_web_search", "fusion_search": "fusion_search"})
        g.add_edge("run_web_search", "request_approval")
        g.add_edge("collect", "analyze_technical")
        g.add_edge("analyze_technical", "assess_risk")
        g.add_edge("assess_risk", "request_approval")
        g.add_edge("request_approval", "synthesize")
    else:
        for n in _INTENT_NODE_NAMES:
            g.add_edge(n, "cache_save_node")
        g.add_edge("fusion_search", "grade_or_critique")
        g.add_conditional_edges("grade_or_critique", decide_next,
            {"synthesize": "synthesize", "web_search": "run_web_search", "fusion_search": "fusion_search"})
        g.add_edge("run_web_search", "synthesize")
        g.add_edge("collect", "analyze_technical")
        g.add_edge("analyze_technical", "assess_risk")
        g.add_edge("assess_risk", "synthesize")

    g.add_edge("synthesize", "cache_save_node")
    g.add_edge("cache_save_node", END)

    return g.compile(checkpointer=checkpointer)


def build_interactive_graph(checkpointer) -> "CompiledGraph":
    """Alias for build_graph(human_approval=True) — kept for backward compatibility."""
    return build_graph(checkpointer=checkpointer, human_approval=True)


def save_graph_image(app, path: str = "agents/graph.png") -> bool:
    """Export graph diagram to PNG. Returns True on success."""
    try:
        app.get_graph().draw_mermaid_png(output_file_path=path)
        return True
    except Exception as e:
        print(f"[graph image] Không xuất được PNG: {e}")
        # Fallback: save mermaid text
        try:
            mermaid_txt = path.replace(".png", ".md")
            Path(mermaid_txt).write_text(
                app.get_graph().draw_mermaid(), encoding="utf-8"
            )
            print(f"[graph image] Đã lưu Mermaid text → {mermaid_txt}")
        except Exception:
            pass
        return False
