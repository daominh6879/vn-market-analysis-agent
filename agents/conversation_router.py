"""
agents/conversation_router.py — LLM-on-top routing for all conversation turns.

Single LLM call at the start of each turn forces an explicit structured decision:
  - Call needs_agent_run(intent, ticker, query) → invoke agent pipeline
  - Call direct_reply(text)                     → stream response, skip agent

Three explicit tool choices — no free-form text escape hatch:
  - needs_agent_run(intent, ticker, query) → invoke agent pipeline
  - direct_reply(text)                     → stream social response, skip agent
  - out_of_scope(text)                     → decline foreign/crypto/forex subject, skip agent
LLM cannot "answer from context"; it must explicitly pick a tool.
Fail-safe: no tool call → re-classify with history; on LLM error → short text error
(never a full market_brief run).
"""

from __future__ import annotations

from typing import TypedDict

from agents.classifier import INTENTS, OUT_OF_SCOPE_REPLY
from agents.focus import Focus, extract_focus_entities, extract_focus_sector, resolve_focus

AGENT_RUN_TOOL: dict = {
    "name": "needs_agent_run",
    "description": (
        "Call this tool whenever the user's message involves a stock, sector, market index, "
        "or any financial data request — regardless of phrasing (direct, implicit, follow-up, comparative). "
        "This includes: new ticker analysis, sector update, market brief, screening, "
        "deeper analysis of previously discussed stocks, or any query that requires fresh market data. "
        "When a follow-up introduces a new subject (e.g., 'what about VCG?', 'còn ngân hàng?'), "
        "treat it as a fresh analysis request for that subject. "
        "When a follow-up only asks to continue/deepen the SAME subject (e.g., 'phân tích sâu hơn', "
        "'phân tích thêm', 'chi tiết hơn', 'more detail', 'elaborate') without naming a new one, "
        "INHERIT the previous turn's subject AND intent. "
        "BUT when the follow-up names the same subject yet a DIFFERENT financial action than the prior "
        "turn (e.g., prior was P/E valuation, now 'giá cổ phiếu' / price), classify the NEW intent from "
        "the current query — do NOT inherit the old intent; use prior context only to fill the ticker. "
        "If the prior turn was about a sector or market "
        "index (e.g., ngân hàng, chỉ số, VN30), keep intent=macro_sector or market_brief and write a "
        "self-contained query that names that sector/index. Only use technical_analysis or price_action "
        "when the prior subject was a single named stock."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [i for i in INTENTS if i not in ("conversation", "out_of_scope")],
                "description": (
                    "The financial intent that should handle this query. "
                    "Use 'macro_sector' for sector-wide or market-index queries "
                    "(e.g., banking sector, construction sector, VN30 index, sector ETF), "
                    "FX/tỷ giá (USD/VND, EUR/VND), interest rates, and commodity prices. "
                    "Use 'price_action' only for a single named stock's price/volume action. "
                    "Use 'market_brief' for broad market overview (VNINDEX, HNX, overall session). "
                    "Use 'screening' for filter/scan queries (ROE > x, P/E < y). "
                    "Use 'investment_case' when asked buy/sell/hold recommendation for a stock. "
                    "Use 'valuation' for a stock's valuation metric (P/E, P/B, ROE, EPS, EV/EBITDA), "
                    "comparing it against sector peers, or comparing two or more stocks against each other. "
                    "For a comparison query (so sánh A và B, A vs B, 'giữa A, B'), set ticker to the FIRST "
                    "ticker and KEEP ALL tickers verbatim in the query field — never drop the 2nd/3rd ticker. "
                    "Use 'rag_qa' for financial-report content (revenue, profit, balance sheet, period figures). "
                    "For continuation follow-ups ('phân tích sâu hơn', 'chi tiết hơn', 'more detail') "
                    "that name no new subject, reuse the prior turn's intent — do NOT default to "
                    "technical_analysis just because the word 'phân tích' appears."
                ),
            },
            "ticker": {
                "type": "string",
                "description": (
                    "Stock ticker (2-5 uppercase letters), or empty string if not applicable. "
                    "Resolve from conversation context when the current query is implicit."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "A self-contained query for the agent. "
                    "Resolve any implicit references using conversation context so the agent "
                    "can execute it without needing the conversation history."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One short sentence explaining this routing decision (for debugging misroutes).",
            },
            "time_context": {
                "type": "object",
                "description": (
                    "Khoảng thời gian câu hỏi ngụ ý, quy về ngày hôm nay (đã nêu trong system). "
                    "Không nêu thời gian ('hiện tại', 'bây giờ', 'gần nhất', 'mới nhất') → "
                    "start_date=null, explicit=false. Tương đối: 'hôm qua'=1 ngày, 'tuần trước'=7 ngày, "
                    "'tháng trước'=30 ngày, 'quý trước'=90 ngày, 'năm ngoái'=365 ngày, "
                    "'N ngày/tháng/tuần/năm'=N×đơn vị. end_date mặc định = hôm nay. "
                    "KHÔNG tự bịa ngày cố định; luôn dùng hôm nay làm mốc."
                ),
                "properties": {
                    "start_date": {"type": ["string", "null"], "description": "ISO YYYY-MM-DD hoặc null."},
                    "end_date": {"type": ["string", "null"], "description": "ISO YYYY-MM-DD hoặc null."},
                    "explicit": {"type": "boolean"},
                },
                "required": ["start_date", "end_date", "explicit"],
            },
        },
        "required": ["intent", "query"],
    },
}

DIRECT_REPLY_TOOL: dict = {
    "name": "direct_reply",
    "description": (
        "Use ONLY for pure social turns that contain no financial subject whatsoever: "
        "greetings, thanks, acknowledgements ('I see', 'makes sense', 'cũng hợp lý'), "
        "simple yes/no reactions to prior results. "
        "Do NOT use if the user's message references any stock ticker, sector name, "
        "market index, or financial metric — even implicitly or as a follow-up. "
        "When in doubt, use needs_agent_run instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Your direct conversational response in Vietnamese.",
            },
            "reason": {
                "type": "string",
                "description": "One short sentence explaining why this is a pure social turn (for debugging).",
            },
        },
        "required": ["text"],
    },
}

OUT_OF_SCOPE_TOOL: dict = {
    "name": "out_of_scope",
    "description": (
        "Use when the user asks about a financial subject OUTSIDE Vietnamese equities: "
        "crypto/bitcoin, US or foreign stocks (Apple, Tesla, ...), or non-VND forex "
        "(EUR/USD, USD/JPY, GBP/USD — pairs with no VND leg). "
        "VND-related FX (USD/VND, EUR/VND, tỷ giá USD) is IN scope — use needs_agent_run "
        "with intent=macro_sector instead of this tool. "
        "Reply politely that you only cover Vietnamese listed securities "
        "(HOSE/HNX/UPCOM) and cannot analyze this subject."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Your polite Vietnamese reply that you only cover Vietnamese equities.",
            },
            "reason": {
                "type": "string",
                "description": "One short sentence naming the out-of-scope subject (for debugging).",
            },
        },
        "required": ["text"],
    },
}

DECOMPOSE_TOOL: dict = {
    "name": "decompose",
    "description": (
        "Use ONLY when the user's message contains TWO OR MORE distinct tasks that must be "
        "answered together in one reply — e.g. a social/general remark PLUS a stock question "
        "(\"hôm nay bạn thế nào? À, FPT có nên mua không?\"), or a supported analysis PLUS an "
        "unsupported request (\"phân tích FPT, và đặt lệnh mua 1.000 cổ phiếu giúp tôi\"). "
        "For a single task, use needs_agent_run / direct_reply / out_of_scope instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "description": (
                    "The independent tasks in this message, in the order you will answer them. "
                    "At most ONE segment may be kind=\"agent\" — merge same-subject financial asks "
                    "into one segment. kind=\"general\" for social remarks (draft the Vietnamese "
                    "reply in \"text\"). kind=\"out_of_scope\" for unsupported requests "
                    "(crypto/foreign stock/trade execution — draft a polite decline in \"text\"). "
                    "kind=\"agent\" for a stock question (set intent/ticker/query like "
                    "needs_agent_run)."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["general", "agent", "out_of_scope"],
                        },
                        "text": {
                            "type": "string",
                            "description": "Drafted Vietnamese reply for general/out_of_scope segments.",
                        },
                        "reason": {"type": "string", "description": "One short note for debugging."},
                        "intent": {
                            "type": "string",
                            "enum": [i for i in INTENTS if i not in ("conversation", "out_of_scope")],
                        },
                        "ticker": {"type": "string", "description": "Stock ticker for the agent segment."},
                        "query": {
                            "type": "string",
                            "description": "Self-contained query for the agent segment.",
                        },
                        "time_context": {"type": "object"},
                    },
                    "required": ["kind"],
                },
            },
        },
        "required": ["segments"],
    },
}


class Segment(TypedDict, total=False):
    """One decomposed task in a mixed-intent message.

    kind="general" / "out_of_scope" carry a drafted `text` reply; kind="agent" carries the
    same fields a needs_agent_run tool input would (intent/ticker/tickers/sector/query/
    time_context) so turn_handler can invoke the graph per segment.
    """
    kind: str        # "general" | "agent" | "out_of_scope"
    text: str        # drafted reply for general / out_of_scope segments
    reason: str      # short debug note
    # agent fields (kind == "agent"):
    intent: str
    ticker: str
    tickers: list[str]
    sector: str
    query: str
    time_context: dict


class RouteResult(TypedDict, total=False):
    type: str       # "agent" | "text" | "mixed"
    intent: str     # present when type == "agent"
    ticker: str     # present when type == "agent" — single first ticker (backward compat)
    tickers: list[str]  # present when type == "agent" — full ticker list (fan-out/comparison)
    sector: str     # present when type == "agent" — sector/index subject (no ticker)
    query: str      # present when type == "agent"
    time_context: dict  # present when type == "agent" — {anchor, start_date, end_date, explicit}
    text: str       # present when type == "text"
    segments: list[dict]  # present when type == "mixed" — list[Segment]
    reason: str     # short routing decision note (for debugging/tracing)


_ROUTE_ERROR_TEXT = (
    "Xin lỗi, tôi gặp lỗi khi phân tích yêu cầu của bạn. Vui lòng thử lại sau."
)


def _inject_last_context(system_prompt: str, focus: Focus, inherit_intent: bool = True) -> str:
    """Append the prior turn's focus (all tickers, and intent only for bare continuations).

    inherit_intent=True  → bare continuation ("phân tích sâu hơn"): the router inherits
                           the prior intent AND subject.
    inherit_intent=False → the current query names its own financial action: inject ONLY
                           the subject (tickers) for resolution, and let the router classify
                           the intent fresh. This is the deterministic guard that prevents
                           a follow-up like "giá cổ phiếu X?" after a P/E turn from being
                           inherited as `valuation` — prompt hints alone were not enough.

    The subject note lists ALL prior tickers, so a continuation after a comparison keeps
    every ticker instead of collapsing to the single first one.
    """
    subject = ", ".join(focus.tickers) if focus.tickers else (focus.sector or "")
    parts = []
    if subject:
        parts.append(f"chủ thể '{subject}'")
    if inherit_intent and focus.intent:
        parts.append(f"intent '{focus.intent}'")
    if not parts:
        return system_prompt
    if inherit_intent:
        note = (
            "\n\nLượt trước: " + ", ".join(parts) + ".\n"
            "Nếu tin nhắn hiện tại là follow-up chỉ yêu cầu tiếp tục/đào sâu chủ thể cũ "
            "(ví dụ 'phân tích sâu hơn', 'phân tích thêm', 'chi tiết hơn') mà không nêu chủ thể mới, "
            "hãy kế thừa intent và chủ thể của lượt trước."
        )
    else:
        note = (
            "\n\nLượt trước chủ thể: " + ", ".join(parts) + ".\n"
            "Câu hiện tại nêu một hành động tài chính riêng — hãy phân loại intent MỚI từ chính câu "
            "hiện tại, KHÔNG kế thừa intent lượt trước. Chỉ dùng chủ thể lượt trước để điền ticker khi "
            "câu hiện tại không nêu mã."
        )
    return system_prompt + note


def _validate_ticker(raw) -> str:
    """Validate a ticker from the LLM against the VN universe.

    Only accepts codes present in the active securities table. Market indices
    (VNINDEX, VN30, ...) are NOT stock tickers — the router LLM handles them via
    market_brief/macro_sector intent (ticker=""), so they drop to "" here and the
    graph's clarify/fan-out path re-resolves the subject instead of fetching a
    nonexistent code. Company names, "NGANHANG", and foreign codes drop too.
    """
    t = (raw or "").strip().upper()
    if not t:
        return ""
    try:
        from core.tickers import get_tickers
        if t in get_tickers():
            return t
    except Exception:
        # Ticker table unavailable (Postgres down) — fall back to a shape check so a
        # plausible code survives instead of silently dropping to "" (which would make
        # every price_action gather fail with "ticker không được rỗng").
        import re
        if re.fullmatch(r"[A-Z]{3}", t):
            return t
    return ""


def _validate_ticker_with_reason(raw, reason: str = "") -> tuple[str, str]:
    """Validate a ticker and fold a dropped hallucination into `reason` for traceability.

    Returns (validated_ticker, reason). Cheap — no behavior change to validation, only an
    extra marker when the LLM named a ticker that failed validation (so a misroute that
    silently dropped to "" can be traced back to what the model actually emitted).
    """
    t = _validate_ticker(raw)
    raw_s = (raw or "").strip()
    if raw_s and not t:
        reason = f"{reason} [ticker_dropped={raw_s}]".strip()
    return t, reason


def _parse_time_context(raw) -> dict:
    """Normalize the router's `time_context` tool input into a stable dict.

    Returns {anchor, start_date, end_date, explicit}. anchor = today (the resolution
    anchor); start_date/end_date are ISO date strings (or None). Never raises.
    """
    from datetime import date
    today = date.today().isoformat()
    raw = raw or {}
    start = raw.get("start_date")
    end = raw.get("end_date")
    return {
        "anchor": today,
        "start_date": str(start)[:10] if start else None,
        "end_date": str(end)[:10] if end else today,
        # Derive explicit from start_date, not the LLM's `explicit` flag: the model can
        # set explicit=false with a real start_date ("tuần trước" vs "tháng trước" would
        # collide on the same cache key) or explicit=true with start_date=null (spurious
        # "..<today>" day-scope). A real start_date is the only reliable signal.
        "explicit": bool(start),
    }


def _parse_agent_fields(
    inp: dict,
    kind: str = "fresh",
    entities: list[str] | None = None,
    query_fallback: str = "",
) -> dict | None:
    """Normalize a needs_agent_run-style tool input into agent routing fields.

    Returns {intent, ticker, tickers, sector, query, time_context, reason}, or None when the
    intent is invalid (caller falls through to re-classify). Shared by the needs_agent_run and
    decompose (agent segment) paths so validation stays in exactly one place.
    """
    intent = inp.get("intent", "")
    if intent not in INTENTS or intent in ("conversation", "out_of_scope"):
        return None
    ticker, reason = _validate_ticker_with_reason(inp.get("ticker"), inp.get("reason", ""))
    time_context = _parse_time_context(inp.get("time_context"))
    q = inp.get("query") or query_fallback
    if kind == "continue" and entities:
        # Bare continuation: force the full prior ticker list so a comparison turn's
        # 2nd/3rd ticker survives even if the LLM dropped it in `query`.
        tickers = list(entities)
        if not ticker:
            ticker = tickers[0]
    else:
        tickers = extract_focus_entities(q) or ([ticker] if ticker else [])
    sector = "" if tickers else extract_focus_sector(q)
    return {
        "intent": intent,
        "ticker": ticker,
        "tickers": tickers,
        "sector": sector,
        "query": q,
        "time_context": time_context,
        "reason": reason,
    }


def _fallback_classify(query: str, history: list[dict], focus: Focus | None = None) -> RouteResult | None:
    """Re-classify via classifier when the router LLM bypassed tools or misused direct_reply.

    Passes conversation history so an implicit follow-up ("phân tích sâu hơn") can resolve
    its subject/ticker from the prior turn instead of classifying with no context.
    Applies the same carry-forward rule as the main path: a bare continuation with a prior
    focus inherits the prior intent + full ticker list instead of being re-classified with
    zero continuation memory (which would silently drop to `conversation`).
    Returns an agent RouteResult for a financial intent, else None (genuine social turn).
    """
    kind = "fresh"
    entities: list[str] = []
    if focus:
        kind, entities = resolve_focus(query, focus)

    if kind == "continue" and focus and focus.intent:
        # Bare continuation — carry the prior intent + whole ticker list forward verbatim,
        # reusing the prior self-contained query (this path never asks the LLM for one).
        tickers = list(entities)  # == prior.tickers (empty for a sector-only focus)
        return RouteResult(
            type="agent",
            intent=focus.intent,
            ticker=tickers[0] if tickers else "",
            tickers=tickers,
            sector=focus.sector,
            query=focus.query or query,
            time_context=_parse_time_context(None),
            reason="fallback_classify_continue",
        )

    try:
        from agents.classifier import classify_hybrid
        r = classify_hybrid(query, messages=history)
    except Exception:
        return None
    if r.intent == "out_of_scope":
        return RouteResult(type="text", text=OUT_OF_SCOPE_REPLY, reason="out_of_scope")
    if r.intent and r.intent != "conversation":
        ticker, reason = _validate_ticker_with_reason(r.ticker, "fallback_classify")
        tickers = extract_focus_entities(query) or ([ticker] if ticker else [])
        return RouteResult(
            type="agent",
            intent=r.intent,
            ticker=ticker,
            tickers=tickers,
            sector="" if tickers else extract_focus_sector(query),
            query=query,
            time_context=_parse_time_context(None),
            reason=reason,
        )
    return None


def _trace(decision: str, intent: str = "", ticker: str = "", fallback_used: bool = False) -> None:
    try:
        from tracing import get_tracer
        get_tracer().event("gate", {
            "node": "llm_route",
            "decision": decision,
            "intent": intent,
            "ticker": ticker,
            "fallback_used": fallback_used,
        })
    except Exception:
        pass


def llm_route(
    query: str,
    history: list[dict],
    system_prompt: str,
    client=None,
    max_tokens: int = 2048,
    focus: Focus | None = None,
) -> RouteResult:
    """Single LLM call that decides: run agent tool OR reply directly.

    Both choices are explicit tool calls — no free-form text path.
    Fail-safe: no tool call → re-classify with history; on LLM error → short text error
    (never a full market_brief run).

    Args:
        query:         Current user message.
        history:       Last N turns [{role, content}] from Postgres.
        system_prompt: Full assistant persona (from _build_system).
        client:        LLM client (create_client() if None).
        max_tokens:    Token budget for direct_reply responses.
        focus:         Prior turn's dialogue focus (from the checkpointer) — for follow-ups.
                       None on the first turn. The router reads resolved slots from it
                       instead of re-deriving continuation/fresh itself.

    Returns:
        RouteResult with type="agent" (needs_agent_run called) or type="text" (direct_reply called).
    """
    kind = "fresh"
    entities: list[str] = []
    if focus:
        # Resolve carry-forward deterministically (no LLM). A message naming a new ticker
        # is FRESH even if it also carries a continuation phrase — never inherited.
        kind, entities = resolve_focus(query, focus)
        if kind == "continue":
            system_prompt = _inject_last_context(system_prompt, focus, inherit_intent=True)
        elif kind == "ambiguous":
            system_prompt = _inject_last_context(system_prompt, focus, inherit_intent=False)
        # "fresh" → no injection: the query names its own entity, prior focus irrelevant.

    # Anchor time resolution to today so the router can map relative phrases to dates.
    from datetime import date as _date
    system_prompt = f"Hôm nay là {_date.today().isoformat()}.\n" + system_prompt

    if client is None:
        from llm.factory import create_client
        client = create_client()

    from llm.types import Message

    msgs: list[Message] = []
    for m in history[-8:]:
        role = m.get("role", "")
        content = m.get("content", "")
        if not (role in ("user", "assistant") and content):
            continue
        if role == "assistant":
            # Keep only enough to confirm a response was given — NOT the data itself.
            # Full reports in history let LLM answer new ticker queries from stale data.
            content = str(content)[:120]
        else:
            content = str(content)[:800]
        msgs.append(Message(role=role, content=content))
    msgs.append(Message(role="user", content=query))

    try:
        resp = client.generate(
            messages=msgs,
            system=system_prompt,
            tools=[AGENT_RUN_TOOL, DIRECT_REPLY_TOOL, OUT_OF_SCOPE_TOOL, DECOMPOSE_TOOL],
            max_tokens=max_tokens,
            temperature=0,
        )
    except Exception:
        _trace("error", fallback_used=True)
        return RouteResult(type="text", text=_ROUTE_ERROR_TEXT, reason="llm_error")

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "needs_agent_run":
            fields = _parse_agent_fields(tc.input, kind=kind, entities=entities, query_fallback=query)
            if fields:
                _trace("agent", intent=fields["intent"], ticker=fields["ticker"])
                return RouteResult(type="agent", **fields)
            # needs_agent_run returned an invalid intent — fall through to re-classify.
        elif tc.name == "decompose":
            segments = []
            for raw in (tc.input.get("segments") or []):
                if not isinstance(raw, dict):
                    continue
                seg_kind = raw.get("kind", "")
                if seg_kind == "agent":
                    fields = _parse_agent_fields(raw, kind="fresh", entities=None, query_fallback=query)
                    if fields:
                        segments.append({"kind": "agent", **fields})
                elif seg_kind == "out_of_scope":
                    text = (raw.get("text") or "").strip() or OUT_OF_SCOPE_REPLY
                    segments.append({"kind": "out_of_scope", "text": text, "reason": raw.get("reason", "")})
                elif seg_kind == "general":
                    text = (raw.get("text") or "").strip()
                    if text:
                        segments.append({"kind": "general", "text": text, "reason": raw.get("reason", "")})
            if segments:
                _trace("mixed")
                return RouteResult(type="mixed", segments=segments, reason=tc.input.get("reason", "decompose"))
            # No usable segments — fall through to re-classify.
        elif tc.name == "out_of_scope":
            # Explicit LLM decision to decline (crypto/US stock/non-VND forex). No
            # fallback classify — that would re-map the foreign subject onto a VN intent.
            # Empty text must still decline — an empty RouteResult text would make
            # turn_handler free-generate on the raw message (answering the crypto
            # question it just refused).
            _trace("out_of_scope")
            return RouteResult(
                type="text",
                text=tc.input.get("text", "").strip() or OUT_OF_SCOPE_REPLY,
                reason=tc.input.get("reason", "out_of_scope"),
            )
        elif tc.name == "direct_reply":
            # Verify with classifier — LLM sometimes misuses direct_reply for financial queries.
            r = _fallback_classify(query, history, focus)
            if r:
                _trace(r.get("type", "agent"), intent=r.get("intent", ""), ticker=r.get("ticker", ""), fallback_used=True)
                return r
            _trace("text")
            text = tc.input.get("text", "").strip()
            if not text:
                # Empty direct_reply text must not reach turn_handler as "" (it would
                # free-generate on the raw message). Re-route to this response's free
                # text, else a safe error — never let empty text escape.
                text = (resp.text or "").strip() or _ROUTE_ERROR_TEXT
            return RouteResult(type="text", text=text, reason=tc.input.get("reason", ""))

    # No (or invalid) tool call — LLM bypassed tools; re-classify with history so an
    # implicit follow-up still resolves its subject. Prevents hallucinated "I don't have
    # data" answers for financial queries.
    r = _fallback_classify(query, history, focus)
    if r:
        _trace(r.get("type", "agent"), intent=r.get("intent", ""), ticker=r.get("ticker", ""), fallback_used=True)
        return r

    # Genuine conversational turn (classifier says "conversation") or classifier failed.
    text = (resp.text or "").strip()
    if text:
        _trace("text", fallback_used=True)
        return RouteResult(type="text", text=text, reason="fallback_free_text")
    _trace("error", fallback_used=True)
    return RouteResult(type="text", text=_ROUTE_ERROR_TEXT, reason="empty_response")
