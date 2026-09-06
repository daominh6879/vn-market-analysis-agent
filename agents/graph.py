"""
agents/graph.py — LangGraph graph: single entry from raw query → report.

Flow:
  classify_node → check_conversation
    └── "conversation" → END  (stream_turn handles LLM streaming)
    └── "out_of_scope" → node_out_of_scope → END
    └── "verify" → clarify_node → _route_after_clarify
          ├── "market_brief" → node_market_brief → finalize_node → END
          ├── "simple"      → build_single_subtask_node ─┐
          ├── "decompose"   → decompose_node ────────────┴→ run_subqueries_node
          ├── "conversation" → finalize_node → END (clarify merged to non-financial)
          └── "out_of_scope" → node_out_of_scope → END
    run_subqueries_node → route_after_subqueries
          ├── "replan"     → decompose_node (≤ 1, on >50% empty sub_results)
          └── "synthesize" → [request_approval] → synthesize_final
    synthesize_final → critique_report_node → route_after_critique
          ├── "save"  → finalize_node → END
          └── "retry" → synthesize_final (≤ MAX_CRITIQUE, folds critique_feedback)

Design rules:
- state stores only paths, never DataFrames
- route_after_subqueries / route_after_critique: pure logic, no LLM
- risk node: pure if/else, no model call
- synthesize_final / critique_report_node: LLM via create_client() factory
- classify_node owns Langfuse trace setup (was in _dispatch_intent)
- every loop is capped (MAX_CRITIQUE, replan_attempted) so cost stays bounded
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.graph import END, StateGraph

from agents.classifier import OUT_OF_SCOPE_REPLY
from agents.state import AgentState
from core.tickers import extract_tickers

log = logging.getLogger(__name__)

MAX_CRITIQUE = 1  # max self-critique retries of the final report before returning best-effort
MAX_FANOUT_TICKERS = 5  # cap per-sector fan-out to leading tickers (avoid 17-way fetch)
GATHER_TIMEOUT_SECONDS = float(os.environ.get("GATHER_TIMEOUT_SECONDS", "30"))
# Per-turn budget guard: cap total graph LLM calls + wall-clock so a pathological turn
# cannot loop unbounded (router call in stream_turn is not counted — it is always 1).
MAX_TURN_LLM_CALLS = int(os.environ.get("MAX_TURN_LLM_CALLS", "8"))
MAX_TURN_SECONDS = float(os.environ.get("MAX_TURN_SECONDS", "180"))


def _llm_budget_exceeded(state: AgentState) -> bool:
    """True when the turn has exhausted its LLM-call or wall-clock budget."""
    if state.get("llm_calls", 0) >= MAX_TURN_LLM_CALLS:
        return True
    started = state.get("turn_started_at")
    return bool(started) and (time.time() - started) > MAX_TURN_SECONDS


def _resolve_time_context(intent: str, query: str) -> dict:
    """Resolve the question to an exact time window (defaults to "now"). Never raises.

    Conversation / out-of-scope turns skip the LLM resolver (no retrieval happens).
    """
    from datetime import date
    today = date.today().isoformat()
    default = {"anchor": today, "start_date": None, "end_date": today, "explicit": False}
    if intent in ("conversation", "out_of_scope"):
        return default
    try:
        from core.time_context import resolve_time_context, to_dict
        return to_dict(resolve_time_context(query))
    except Exception:
        return default

# Intents that require multi-angle decomposition — always go through decompose_node.
# All others use the fast path (build_single_subtask_node) when a ticker is present.
_COMPLEX_INTENTS = frozenset({"market_brief", "investment_case", "macro_sector"})
# Leaf intents that are sector-wide (no single ticker): they must NOT decompose, but
# they also can't take the ticker-gated "simple" branch above. Route them to the fast
# path with an empty ticker — their gather_data resolves the subject from the query
# itself (screening resolves its sector; breakout_scan scans sector or full market).
_SECTOR_LEAF_INTENTS = frozenset({"screening", "breakout_scan"})

def _looks_like_screening(query: str) -> bool:
    """Alias of agents.classifier.is_screening_query (graph guard + tests)."""
    from agents.classifier import is_screening_query
    return is_screening_query(query)


# ── Node −1: classify_node ────────────────────────────────────────────────────


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

    try:
        from tracing import get_tracer
        get_tracer().turn_start(
            state.get("query", ""),
            intent=state.get("intent", ""),
            ticker=state.get("ticker", ""),
        )
    except Exception:
        pass

    # Pre-classified by conversation_router reroute — skip LLM classification.
    pre_intent = state.get("intent", "")
    screening_filter = state.get("screening_filter")
    if pre_intent and pre_intent != "conversation":
        from dataclasses import dataclass
        @dataclass
        class _R:
            intent: str
            ticker: str | None
            reason: str
        result = _R(pre_intent, state.get("ticker") or None, "pre-classified:reroute")
    else:
        from agents.classifier import classify_hybrid
        result = classify_hybrid(
            state.get("query", ""),
            messages=state.get("messages"),
        )
        screening_filter = result.screening or screening_filter

    # Deterministic guard: "lọc RSI < 30" is screening even when the LLM leans
    # technical_analysis (RSI keyword) or macro_sector — force it so it routes to the
    # fast path instead of decompose. Never override a decline. Check both the router's
    # rewritten query and the verbatim original (a rewrite can drop the "lọc" keyword).
    q = state.get("query", "") or state.get("original_query", "")
    if _looks_like_screening(q) and result.intent not in ("out_of_scope", "conversation"):
        result.intent = "screening"
        result.reason = f"{result.reason} [screening:lọc]"

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

    # Router-provided time_context (stream_turn sets it from llm_route) wins; otherwise
    # resolve here — only for direct graph invocation (tests / clarify resume), never in
    # the production router path.
    time_context = state.get("time_context") or _resolve_time_context(result.intent, state.get("query", ""))
    return {
        "intent": result.intent,
        "ticker": result.ticker or "",
        "screening_filter": screening_filter,
        "classify_reason": result.reason,
        "time_context": time_context,
    }


def check_conversation(state: AgentState) -> str:
    """Skip clarify for conversation / out-of-scope turns — no graph work needed."""
    intent = state.get("intent", "")
    if intent == "conversation":
        return "skip"
    if intent == "out_of_scope":
        return "out_of_scope"
    return "verify"


def node_out_of_scope(state: AgentState) -> dict:
    """Fixed decline for out-of-scope subjects (crypto/foreign/forex) — no gather, no LLM."""
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {"node": "out_of_scope"})
    except Exception:
        pass
    return {"report": OUT_OF_SCOPE_REPLY, "intent": "out_of_scope"}


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
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "clarify_interrupt",
            "question": question[:200],
            "pending_type": pending.__class__.__name__ if pending else "",
        })
    except Exception:
        pass
    answer = interrupt(question)  # pauses graph; resumes when user replies

    merged_query = merge_with_pending(pending, answer)
    from agents.classifier import classify_hybrid
    result = classify_hybrid(merged_query, messages=state.get("messages"))

    report = ""
    if result.intent == "conversation":
        # Clarify re-classified the merged answer as non-financial (e.g. "bỏ qua").
        # The "conversation" route exits via END with report="" → blank reply.
        # Acknowledge so the turn always returns something.
        report = "Đã rõ. Tôi sẵn sàng hỗ trợ phân tích chứng khoán Việt Nam khi bạn cần."

    tickers = extract_tickers(merged_query) or ([result.ticker] if result.ticker else [])
    sector = ""
    if not tickers:
        from agents.focus import extract_focus_sector
        sector = extract_focus_sector(merged_query)
    return {
        "query": merged_query,
        "intent": result.intent,
        "ticker": result.ticker or "",
        "tickers": tickers,
        "sector": sector,
        "classify_reason": result.reason,
        "report": report,
        # Resume reuses the checkpoint state from when the interrupt was created, which
        # may be minutes/hours old — reset the per-turn budget so replan/critique-retry
        # aren't silently skipped on every resumed turn (_llm_budget_exceeded).
        "turn_started_at": time.time(),
        "llm_calls": 0,
    }


def node_market_brief(state: AgentState) -> dict:
    from datetime import date
    from agents.market_brief_graph import build_brief_graph, make_initial_state as mb_init
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {"node": "market_brief_start", "intent": "market_brief"})
    except Exception:
        pass
    app = build_brief_graph()
    initial = mb_init(date=str(date.today()), output_path="")
    final = app.invoke(initial)
    report = final.get("report_text") or "[Không có báo cáo thị trường]"
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {"node": "market_brief_done", "report_len": len(report)})
    except Exception:
        pass
    return {"report": report}


# ── finalize_node ─────────────────────────────────────────────────────────────

def finalize_node(state: AgentState) -> dict:
    """Close the turn's trace span after a successful report (no response cache)."""
    report = state.get("report") or ""
    try:
        from tracing import get_tracer
        get_tracer().turn_end(
            intent=state.get("intent", ""),
            ticker=state.get("ticker", ""),
            report_len=len(report),
            cache_hit=False,
        )
    except Exception:
        pass
    return {}


# ── New pipeline nodes ────────────────────────────────────────────────────────

def decompose_node(state: AgentState) -> dict:
    """Decompose original query into structured sub-tasks, each with its own intent.

    On re-plan (replan_note set after empty sub_results), folds the failure note into
    the decompose prompt so a different set of sub-tasks is produced, and marks
    replan_attempted so the loop runs at most once.
    """
    from rag.multi_query import generate_sub_tasks
    parent_intent = state.get("intent", "macro_sector")
    ticker = state.get("ticker", "")
    query_tickers = (
        extract_tickers(state.get("original_query", ""))
        or extract_tickers(state.get("query", ""))
    )
    fallback_tickers = (query_tickers or ([ticker] if ticker else []))[:MAX_FANOUT_TICKERS]
    replan_note = state.get("replan_note", "")
    base_query = state.get("query", "")
    decompose_query = f"{replan_note}\n\nCâu hỏi gốc: {base_query}" if replan_note else base_query
    sub_tasks = []
    for t in generate_sub_tasks(decompose_query, n=4):
        # The LLM already names the notable tickers inside each sub-task question
        # (prompt caps it at ≤5). Prefer those; fall back to query-level extraction.
        task_tickers = (extract_tickers(t.get("question", "")) or fallback_tickers)
        sub_tasks.append({
            "intent": t.get("intent") or parent_intent,
            "tickers": task_tickers[:MAX_FANOUT_TICKERS],
            "question": t.get("question", ""),
        })
    try:
        print(f"[decompose] {len(sub_tasks)} sub-tasks:")
        for i, t in enumerate(sub_tasks, 1):
            print(f"  {i}. [{t['intent']}] tickers={t['tickers']} | {t['question'][:80]}")
    except UnicodeEncodeError:
        # stdout is a non-UTF8 console (e.g. cp1252) — debug print must not abort the turn.
        pass
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "decompose",
            "sub_tasks": len(sub_tasks),
            "questions": [t["question"][:80] for t in sub_tasks],
            "intent": parent_intent,
            "ticker": ticker,
        })
    except Exception:
        pass
    return {
        "sub_tasks": sub_tasks,
        "replan_attempted": bool(replan_note),
        "replan_note": "",
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


_GATHER_MAP: dict[str, object] = {}  # populated lazily below


def _get_gather_map() -> dict:
    if not _GATHER_MAP:
        from agents.intents import (
            price_action, technical, news_sentiment, fundamentals,
            macro_sector, investment_case, screening, breakout,
        )
        from rag import qa as _qa

        _GATHER_MAP.update({
            "price_action":       lambda t, q, tc=None: price_action.gather_data(t, q, tc),
            "technical_analysis": lambda t, q, tc=None: technical.gather_data(t, q, tc),
            "news_sentiment":     lambda t, q, tc=None: news_sentiment.gather_data(t, q, tc),
            "macro_sector":       lambda t, q, tc=None: macro_sector.gather_data(t, q, tc),
            "investment_case":    lambda t, q, tc=None: investment_case.gather_data(t, q, tc),
            "screening":          lambda t, q, tc=None, sf=None: screening.gather_data(t, q, tc, screening=sf),
            "rag_qa":             lambda t, q, tc=None: _qa.retrieve_only(q, ticker=t),
            "valuation":          lambda t, q, tc=None, tickers=None: fundamentals.gather_data(t, q, tc, tickers=tickers),
            "breakout_scan":      lambda t, q, tc=None: breakout.gather_data(t, q, tc),
            "market_brief":       lambda t, q, tc=None: "[market_brief: xem riêng]",
        })
    return _GATHER_MAP


_FANOUT_INTENTS = frozenset({"price_action", "technical_analysis", "investment_case", "breakout_scan"})


def _is_empty_result(data: str) -> bool:
    """True when a gather result carries no usable data (empty, or an error string)."""
    if not data or not data.strip():
        return True
    low = data.lower()
    return any(m in low for m in (
        "lỗi:", "không có dữ liệu", "no_data", "không thể lấy", "ticker không được rỗng",
    ))


def _is_gather_error(s: str) -> bool:
    """True when a gather entry is an error marker emitted by _safe_gather/_gather_tickers."""
    return "— lỗi:" in s


def _safe_gather(fn, ticker: str, question: str, tc: dict | None = None, **kw) -> str:
    """Call a gather fn, converting any error to an inline error string (never raises).

    tc (time_context) is passed only when non-None, so 2-arg gather fns (unit tests,
    direct gather_data calls) keep working unchanged. Extra kwargs (e.g. screening) are
    forwarded to fn.
    """
    try:
        if tc is None:
            return fn(ticker, question, **kw) or ""
        return fn(ticker, question, tc, **kw) or ""
    except Exception as exc:
        return f"[{ticker} — lỗi: {exc}]"


def _gather_tickers(fn, tickers: list[str], question: str, tc: dict | None = None) -> str:
    """Fan-out a gather fn over multiple tickers in parallel, preserving input order.

    Futures run in parallel under a shared GATHER_TIMEOUT_SECONDS deadline — awaiting
    each in order with a per-future timeout would let N hangs eat N×30s serially.
    """
    if not tickers:
        return ""
    if len(tickers) == 1:
        return _safe_gather(fn, tickers[0], question, tc)
    results: dict[str, str] = {}
    ex = ThreadPoolExecutor(max_workers=min(len(tickers), MAX_FANOUT_TICKERS))
    try:
        futs = [(ex.submit(_safe_gather, fn, t, question, tc), t) for t in tickers]
        # Shared deadline, not per-future: awaiting each in order with a per-future
        # timeout lets N hanging futures eat N×GATHER_TIMEOUT_SECONDS serially (5×30s
        # = 150s vs MAX_TURN_SECONDS=180). Each future gets the remaining budget.
        deadline = time.monotonic() + GATHER_TIMEOUT_SECONDS
        for fut, t in futs:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                results[t] = fut.result(timeout=remaining) or ""
            except Exception as exc:
                results[t] = f"[{t} — lỗi: {exc}]"
    finally:
        # wait=False: a hung gather thread must not stall the turn at executor exit.
        # The tool layer (VCI/yfinance) carries its own HTTP timeout as the real bound.
        ex.shutdown(wait=False, cancel_futures=True)
    # Drop per-ticker error markers when at least one ticker returned data, so a 1-of-N
    # failure doesn't make the whole fan-out read as "lỗi:" (spurious replan) and isn't
    # mixed into otherwise-good data for synthesis. All-failed keeps the error detail.
    entries = [results.get(t, "") for t in tickers]
    ok = [e for e in entries if e and not _is_gather_error(e)]
    if ok:
        return "\n\n".join(ok)
    return "\n\n".join(e for e in entries if e)


def run_subqueries_node(state: AgentState) -> dict:
    """Gather data for each structured sub-task — no LLM, no re-classification.

    Reads sub_tasks [{intent, tickers, question}] set by decompose_node. Fan-out intents
    fetch each ticker in parallel (ThreadPoolExecutor) with a per-fetch timeout so one
    hung source cannot stall the turn. Sub-tasks stay sequential: the in-memory tool TTL
    cache (tools/cache.py) is not concurrency-safe, so parallelising sub-tasks would
    re-introduce the duplicate-news-fetch regression this node is meant to avoid.
    """
    gather = _get_gather_map()
    sub_tasks = state.get("sub_tasks") or []
    fallback_ticker = state.get("ticker", "")
    fallback_query = state.get("query", "")
    tc = state.get("time_context")

    sub_results: list[str] = []
    usable = 0
    for task in sub_tasks:
        intent = task.get("intent", "macro_sector")
        tickers = task.get("tickers") or ([fallback_ticker] if fallback_ticker else [])
        question = task.get("question") or fallback_query
        fn = gather.get(intent)
        if fn is None:
            # Unknown intent slipped through decompose — surface it, then fall back to
            # macro_sector so the turn still produces data instead of a silent empty.
            log.warning("gather_unknown_intent intent=%s question=%r", intent, question[:80])
            try:
                from tracing import get_tracer
                get_tracer().event("gate", {
                    "node": "gather_unknown_intent",
                    "intent": intent,
                    "question": question[:80],
                })
            except Exception:
                pass
            fn = gather.get("macro_sector")
            intent = "macro_sector"

        if intent in _FANOUT_INTENTS and tickers:
            data = _gather_tickers(fn, tickers, question, tc)
        else:
            primary = tickers[0] if tickers else fallback_ticker
            if intent == "screening":
                data = _safe_gather(fn, primary, question, tc, screening=task.get("screening"))
            elif intent == "valuation":
                data = _safe_gather(fn, primary, question, tc, tickers=tickers)
            else:
                data = _safe_gather(fn, primary, question, tc)

        # Judge emptiness on the raw gather output, not the composed label string —
        # the question/label text must not flip a task to "empty".
        if not _is_empty_result(data):
            usable += 1
        if data:
            tickers_label = "+".join(tickers) if tickers else "N/A"
            sub_results.append(f"[{intent.upper()} — {tickers_label}]\n{question}\n{data}")
        try:
            from tracing import get_tracer
            get_tracer().event("tool", {
                "tool": f"gather:{intent}",
                "args": {"tickers": tickers, "question": question[:80]},
                "status": "ok" if data else "empty",
                "preview": (data or "")[:120],
                "duration_ms": 0,
            })
        except Exception:
            pass

    total = len(sub_tasks)
    empty_ratio = (total - usable) / total if total else 1.0

    updates: dict = {"sub_results": sub_results, "sub_results_empty_ratio": empty_ratio}
    if empty_ratio > 0.5 and not state.get("replan_attempted", False):
        failed = [t.get("question", "")[:60] for t in sub_tasks][:3]
        updates["replan_note"] = (
            "Lần phân rã trước không lấy được dữ liệu cho các truy vấn con (rỗng/lỗi): "
            + "; ".join(failed)
            + ". Hãy phân rã lại thành các câu hỏi con KHÁC, góc nhìn khác, cụ thể hơn."
        )
    return updates


def _is_label_line(line: str) -> bool:
    """True for a bare `[...]` line — the internal markers the pipeline wraps data in."""
    s = line.strip()
    return (
        s.startswith("[") and s.endswith("]")
        and s.count("[") == 1 and s.count("]") == 1
    )


def _strip_subresult_labels(sub_results: list[str]) -> str:
    """Join gather results into synthesis context, dropping only the outer wrapper.

    Each sub_result is "[{INTENT} — {TICKER}]\n{question}\n{data}" (run_subqueries_node);
    `data` itself opens with a "[MARKER TICKER]" header (gather_data). Drop the outer
    "[{INTENT} — {TICKER}]" label (pure plumbing) and, for a single sub-task, the
    redundant question line (it may be the router's rewritten query). KEEP the inner
    "[MARKER]" headers — they name the data source (price / technical / valuation / news)
    and the LLM needs them to synthesize a decomposed multi-source context; the system
    prompt instructs the LLM not to quote them or add a "Lưu ý" disclaimer.
    """
    single = len(sub_results) == 1
    cleaned: list[str] = []
    for block in sub_results:
        lines = block.splitlines()
        if lines and _is_label_line(lines[0]):
            lines = lines[1:]  # outer "[INTENT — TICKER]" label
        if single and lines and not _is_label_line(lines[0]):
            lines = lines[1:]  # redundant question line (single sub-task)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines:
            cleaned.append("\n".join(lines))
    return "\n\n---\n\n".join(cleaned)


def synthesize_final(state: AgentState) -> dict:
    """Single LLM call over all gathered sub-results. Respects STRICT_NEUTRAL env flag."""
    from llm.factory import create_client
    from llm.types import Message

    sub_results = state.get("sub_results") or []
    # Answer the user's verbatim question, not the router's rewritten "self-contained"
    # query — otherwise a simple "giá cổ phiếu X?" echoes as "cho tôi phân tích tổng hợp...".
    query = state.get("original_query") or state.get("query", "")
    context = _strip_subresult_labels(sub_results)

    # Date-aware synthesis: anchor the report to the resolved time window.
    tc = state.get("time_context") or {}
    anchor = tc.get("anchor") or ""
    start = tc.get("start_date")
    end = tc.get("end_date")
    if start and end:
        time_line = f"Hôm nay: {anchor}. Khoảng thời gian yêu cầu: {start} → {end}."
    elif anchor:
        time_line = f"Hôm nay: {anchor}. Dùng dữ liệu mới nhất hiện có."
    else:
        time_line = ""

    strict = os.environ.get("STRICT_NEUTRAL", "false").lower() == "true"
    if strict:
        user_prompt = (
            f"Tổng hợp phân tích DỮ KIỆN từ ngữ cảnh. Trình bày trung lập.\n"
            f"{time_line}\nNgữ cảnh: {context}\nCâu hỏi: {query}"
        )
    else:
        user_prompt = (
            f"Tổng hợp phân tích từ ngữ cảnh và trả lời câu hỏi.\n"
            f"{time_line}\nNgữ cảnh: {context}\nCâu hỏi: {query}"
        )

    strict_note = " TUYỆT ĐỐI không đưa khuyến nghị mua/bán/nắm giữ." if strict else ""
    # Block-count-aware synthesis rule: the old fixed prompt forced "synthesize ALL
    # blocks, miss none" even when the context held a single block, so the model added
    # an empty "tổng hợp các khối khác" section plus a redundant "kết luận". Scope the
    # multi-block instruction to multi-block contexts only.
    n_blocks = len(sub_results)
    if n_blocks > 1:
        block_rule = (
            "Ngữ cảnh chứa NHIỀU khối dữ liệu từ nhiều nguồn khác nhau "
            "(giá, kỹ thuật, định giá, tin tức, vĩ mô...). "
            "Tổng hợp ĐẦY ĐỦ TẤT CẢ các khối, mỗi khối thành một phần riêng, "
            "không bỏ sót khối nào, không chỉ trả lời về một khối duy nhất. "
        )
    else:
        block_rule = (
            "Ngữ cảnh chỉ có một khối dữ liệu. "
            "Trả lời trực tiếp câu hỏi bằng chính số liệu trong khối đó. "
        )
    system_prompt = (
        "Bạn là chuyên gia phân tích tài chính Việt Nam. "
        "Trả lời bằng Markdown, trích dẫn số liệu cụ thể từ ngữ cảnh. "
        + block_rule
        + "KHÔNG thêm mục 'Tổng hợp các khối khác', 'Kết luận', 'Lưu ý', "
        "hay chú thích rằng ngữ cảnh thiếu/đủ dữ liệu. "
        "KHÔNG nhắc lại các nhãn nội bộ trong ngoặc vuông."
        + strict_note
    )
    critique_feedback = state.get("critique_feedback", "")
    if critique_feedback:
        user_prompt += (
            "\n\nLƯU Ý: báo cáo trước bị reviewer đánh fail với lý do sau, "
            "bắt buộc khắc phục trong bản viết lại:\n" + critique_feedback
        )
    messages = [Message(role="user", content=user_prompt)]
    t0 = time.perf_counter()
    client = create_client()

    # Stream final report token-by-token → SSE client (emit_llm_delta). Falls back to
    # generate() if the provider stream fails OR yields nothing, so a turn never dies
    # on stream errors and never returns an empty report.
    report = ""
    streamed = False
    # Only the first attempt streams: a critique retry must NOT emit_llm_delta again,
    # or the SSE client receives draft1 + draft2 concatenated while assistant_reply
    # keeps only the last one (streamed text ≠ saved reply).
    is_retry = state.get("critique_attempts", 0) > 0
    if not is_retry:
        try:
            from tracing import emit_llm_delta
            parts: list[str] = []
            for chunk in client.stream(messages, max_tokens=4000, system=system_prompt):
                parts.append(chunk)
                emit_llm_delta(chunk)
            report = "".join(parts).strip()
            streamed = bool(parts)
        except Exception:
            streamed = False

    if not streamed:
        resp = client.generate(messages, max_tokens=4000, system=system_prompt)
        report = resp.text.strip()

    elapsed = time.perf_counter() - t0

    # Token usage for streamed calls lives in usage.jsonl (written by instrument_llm).
    # Estimate here only for the in-graph history ledger.
    from llm.pricing import estimate_tokens
    in_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
    out_tokens = estimate_tokens(report)

    return {
        "report": report,
        "summary": report[:120],
        "report_candidates": state.get("report_candidates", []) + [report],
        "step_count": state.get("step_count", 0) + 1,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "history": state.get("history", []) + [{
            "step": "synthesize_final",
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "elapsed_seconds": round(elapsed, 2),
            "streamed": streamed,
        }],
    }

# ── Self-critique loop ────────────────────────────────────────────────────────

def critique_report_node(state: AgentState) -> dict:
    """LLM self-check of the synthesized report against the completion checklist.

    Returns {critique_pass, critique_feedback, critique_attempts}. Feedback folds into
    synthesize_final on retry (≤ MAX_CRITIQUE). Any parse/LLM error → pass=True, so a
    broken critique never blocks the turn.
    """
    from llm.factory import create_client
    from llm.types import Message

    report = state.get("report") or ""
    query = state.get("query", "")
    attempts = state.get("critique_attempts", 0)

    prompt = f"""Câu hỏi người dùng: {query}

Báo cáo đã viết:
---
{report}
---

Đánh giá báo cáo theo checklist:
1. Mọi số liệu / claim cụ thể phải có nguồn trích dẫn; không bịa số không có trong ngữ cảnh.
2. Báo cáo đủ phần, không bị cắt ngang giữa chừng (không truncation).
3. Trả lời đúng câu hỏi, không lan man.

Chỉ trả về JSON: {{"pass": true|false, "feedback": "lý do ngắn gọn tiếng Việt nếu fail"}}."""

    client = create_client()
    passed = True
    feedback = ""
    try:
        import json as _json
        import re as _re
        resp = client.generate(
            [Message(role="user", content=prompt)],
            max_tokens=300,
            temperature=0,
            system="Bạn là reviewer chất lượng báo cáo tài chính. Chỉ trả JSON, không giải thích.",
        )
        raw = _re.sub(r"<think>.*?</think>", "", resp.text.strip(), flags=_re.DOTALL).strip()
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        data = _json.loads(m.group(0)) if m else {}
        passed = bool(data.get("pass", True))
        feedback = str(data.get("feedback", ""))
    except Exception:
        passed = True
        feedback = ""

    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "critique_report",
            "pass": passed,
            "attempt": attempts + 1,
        })
    except Exception:
        pass

    attempts_after = attempts + 1
    critique_results = list(state.get("critique_results", [])) + [passed]
    updates: dict = {
        "critique_pass": passed,
        "critique_feedback": feedback if not passed else "",
        "critique_attempts": attempts_after,
        "critique_results": critique_results,
        "llm_calls": state.get("llm_calls", 0) + 1,
    }
    # Keep the best report: when retries are exhausted and this candidate also failed,
    # prefer any candidate that passed critique; otherwise fall back to the first
    # candidate so a worse retry never overwrites a better first draft.
    if attempts_after > MAX_CRITIQUE and not passed:
        candidates = list(state.get("report_candidates", []))
        if any(critique_results):
            updates["report"] = next(
                (c for c, ok in zip(candidates, critique_results) if ok),
                candidates[0] if candidates else report,
            )
        elif candidates:
            updates["report"] = candidates[0]
    return updates


def route_after_critique(state: AgentState) -> str:
    """Retry synthesize once with feedback; otherwise save best-effort report.

    Budget guard short-circuits first: once the turn's LLM/wall-clock budget is gone,
    skip the retry (another synthesize + critique = 2 more LLM calls) and save.
    """
    if _llm_budget_exceeded(state):
        return "save"
    if state.get("critique_pass", True):
        return "save"
    if state.get("critique_attempts", 0) <= MAX_CRITIQUE:
        return "retry"
    return "save"


def route_after_subqueries(state: AgentState) -> str:
    """Re-plan once if most sub-tasks returned no usable data; else synthesize.

    Budget guard first: no re-plan (another decompose LLM call) when budget is gone.
    """
    if _llm_budget_exceeded(state):
        return "synthesize"
    ratio = state.get("sub_results_empty_ratio", 0.0)
    if ratio > 0.5 and not state.get("replan_attempted", False):
        return "replan"
    return "synthesize"


# ── Fast-path routing ────────────────────────────────────────────────────────

def _route_after_clarify(state: AgentState) -> str:
    """Skip decompose for single-ticker leaf-intent queries — saves 1 LLM call + 3 data fetches."""
    intent = state.get("intent", "")
    ticker = state.get("ticker", "")
    if intent == "out_of_scope":
        # Clarify re-classified the merged answer as out-of-scope (e.g. user said "thôi
        # tôi muốn hỏi bitcoin"). Decline directly — no decompose/fabrication.
        route = "out_of_scope"
    elif intent == "conversation":
        # Clarify re-classified the merged answer as non-financial (e.g. user said "bỏ
        # qua"). Exit — decompose would run with parent_intent="conversation" and gather
        # empty, producing a fabricated report.
        route = "conversation"
    elif intent == "market_brief":
        route = "market_brief"
    elif intent in _SECTOR_LEAF_INTENTS:
        route = "simple"
    elif (intent
            and intent not in _COMPLEX_INTENTS
            and ticker):
        route = "simple"
    else:
        route = "decompose"
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "route_after_clarify",
            "route": route,
            "intent": intent,
            "ticker": ticker,
        })
    except Exception:
        pass
    return route


def build_single_subtask_node(state: AgentState) -> dict:
    """Wrap pre-classified intent+ticker into a single sub-task, bypassing decompose_node."""
    intent = state.get("intent", "macro_sector")
    ticker = state.get("ticker", "")
    query = state.get("query", "")
    original = state.get("original_query", "")

    stored_tickers = state.get("tickers") or []
    # Multi-ticker comparison: the LLM-expanded `query` may drop the 2nd/3rd ticker.
    # original_query is verbatim — when it names ≥2 known tickers, prefer it so gather_data
    # (e.g. valuation) can cross-compare both. Applies regardless of the ticker-list source:
    # the `tickers` list can be right (from the router) while `query` is not.
    if len(extract_tickers(query)) < 2 and len(extract_tickers(original)) >= 2:
        query = original

    if stored_tickers:
        # Router carried the full ticker list end-to-end (via Focus) — trust it, no
        # second independent re-extraction (which would drop a comparison's 2nd/3rd ticker).
        tickers = list(stored_tickers)
    else:
        # Direct-graph-invocation callers (tests) never went through the router: re-extract.
        tickers = extract_tickers(query) or ([ticker] if ticker else [])

    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "fast_path",
            "intent": intent,
            "ticker": ticker,
            "tickers": tickers,
            "query": query[:80],
        })
    except Exception:
        pass
    return {
        "sub_tasks": [{
            "intent": intent,
            "tickers": tickers,
            "question": query,
            "screening": state.get("screening_filter"),
        }],
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def _request_approval(state: AgentState) -> dict:
    """Pause for human review. interrupt() suspends graph until resumed via Command(resume=...)."""
    from langgraph.types import interrupt
    sub_results = state.get("sub_results") or []
    proposal = {
        "ticker": state.get("ticker"),
        "query": state.get("query", ""),
        "data_preview": "\n\n".join(sub_results)[:1500],
    }
    decision = interrupt(proposal)
    if decision is False:
        return {"error": "rejected_by_user"}
    return {}


def _check_approval_decision(state: AgentState) -> str:
    """Route to END on rejection, synthesize_final on approval."""
    return "end" if state.get("error") == "rejected_by_user" else "synthesize_final"


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

    g.add_node("classify_node",            classify_node)
    g.add_node("clarify_node",             clarify_node)
    g.add_node("node_market_brief",        node_market_brief)
    g.add_node("node_out_of_scope",        node_out_of_scope)
    g.add_node("build_single_subtask_node", build_single_subtask_node)
    g.add_node("decompose_node",           decompose_node)
    g.add_node("run_subqueries_node",      run_subqueries_node)
    g.add_node("synthesize_final",         synthesize_final)
    g.add_node("critique_report_node",     critique_report_node)
    g.add_node("finalize_node",            finalize_node)

    if human_approval:
        g.add_node("request_approval", _request_approval)

    g.set_entry_point("classify_node")
    g.add_conditional_edges("classify_node", check_conversation,
        {"skip": END, "out_of_scope": "node_out_of_scope", "verify": "clarify_node"})
    g.add_conditional_edges("clarify_node", _route_after_clarify,
        {"market_brief": "node_market_brief",
         "simple": "build_single_subtask_node",
         "decompose": "decompose_node",
         "conversation": "finalize_node",
         "out_of_scope": "node_out_of_scope"})
    g.add_edge("node_market_brief", "finalize_node")
    g.add_edge("node_out_of_scope", END)
    g.add_edge("build_single_subtask_node", "run_subqueries_node")
    g.add_edge("decompose_node",            "run_subqueries_node")

    # run_subqueries → re-plan once on mostly-empty results, else continue to synthesize.
    next_after_subqueries = "request_approval" if human_approval else "synthesize_final"
    g.add_conditional_edges("run_subqueries_node", route_after_subqueries,
        {"replan": "decompose_node", "synthesize": next_after_subqueries})

    if human_approval:
        g.add_conditional_edges("request_approval", _check_approval_decision,
            {"end": END, "synthesize_final": "synthesize_final"})

    g.add_edge("synthesize_final", "critique_report_node")
    g.add_conditional_edges("critique_report_node", route_after_critique,
        {"save": "finalize_node", "retry": "synthesize_final"})

    g.add_edge("finalize_node",     END)

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
            return True
        except Exception:
            pass
        return False
