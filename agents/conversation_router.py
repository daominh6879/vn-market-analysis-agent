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
        "INHERIT the previous turn's subject AND intent. If the prior turn was about a sector or market "
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


class RouteResult(TypedDict, total=False):
    type: str       # "agent" | "text"
    intent: str     # present when type == "agent"
    ticker: str     # present when type == "agent"
    query: str      # present when type == "agent"
    text: str       # present when type == "text"
    reason: str     # short routing decision note (for debugging/tracing)


_ROUTE_ERROR_TEXT = (
    "Xin lỗi, tôi gặp lỗi khi phân tích yêu cầu của bạn. Vui lòng thử lại sau."
)


def _inject_last_context(system_prompt: str, last_intent: str, last_subject: str) -> str:
    """Append the prior turn's intent/subject so the router can resolve follow-ups.

    Replaces the reliance on truncated assistant history (120 chars) with an explicit
    signal. When the current message is a bare continuation ("phân tích sâu hơn"), the
    router inherits this intent/subject instead of guessing.
    """
    parts = []
    if last_subject:
        parts.append(f"chủ thể '{last_subject}'")
    if last_intent:
        parts.append(f"intent '{last_intent}'")
    if not parts:
        return system_prompt
    note = (
        "\n\nLượt trước: " + ", ".join(parts) + ".\n"
        "Nếu tin nhắn hiện tại là follow-up chỉ yêu cầu tiếp tục/đào sâu chủ thể cũ "
        "(ví dụ 'phân tích sâu hơn', 'phân tích thêm', 'chi tiết hơn') mà không nêu chủ thể mới, "
        "hãy kế thừa intent và chủ thể của lượt trước."
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
        pass
    return ""


def _fallback_classify(query: str, history: list[dict]) -> RouteResult | None:
    """Re-classify via classifier when the router LLM bypassed tools or misused direct_reply.

    Passes conversation history so an implicit follow-up ("phân tích sâu hơn") can resolve
    its subject/ticker from the prior turn instead of classifying with no context.
    Returns an agent RouteResult for a financial intent, else None (genuine social turn).
    """
    try:
        from agents.classifier import classify_hybrid
        r = classify_hybrid(query, messages=history)
    except Exception:
        return None
    if r.intent == "out_of_scope":
        return RouteResult(type="text", text=OUT_OF_SCOPE_REPLY, reason="out_of_scope")
    if r.intent and r.intent != "conversation":
        return RouteResult(
            type="agent",
            intent=r.intent,
            ticker=_validate_ticker(r.ticker),
            query=query,
            reason="fallback_classify",
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
    last_intent: str = "",
    last_subject: str = "",
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
        last_intent:   Prior turn's resolved intent (from checkpoint state) — for follow-ups.
        last_subject:  Prior turn's subject ticker (from checkpoint state) — for follow-ups.

    Returns:
        RouteResult with type="agent" (needs_agent_run called) or type="text" (direct_reply called).
    """
    if last_intent or last_subject:
        system_prompt = _inject_last_context(system_prompt, last_intent, last_subject)

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
            tools=[AGENT_RUN_TOOL, DIRECT_REPLY_TOOL, OUT_OF_SCOPE_TOOL],
            max_tokens=max_tokens,
            temperature=0,
        )
    except Exception:
        _trace("error", fallback_used=True)
        return RouteResult(type="text", text=_ROUTE_ERROR_TEXT, reason="llm_error")

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "needs_agent_run":
            inp = tc.input
            intent = inp.get("intent", "")
            if intent in INTENTS and intent not in ("conversation", "out_of_scope"):
                ticker = _validate_ticker(inp.get("ticker"))
                _trace("agent", intent=intent, ticker=ticker)
                return RouteResult(
                    type="agent",
                    intent=intent,
                    ticker=ticker,
                    query=inp.get("query") or query,
                    reason=inp.get("reason", ""),
                )
            # needs_agent_run returned an invalid intent — fall through to re-classify.
        elif tc.name == "out_of_scope":
            # Explicit LLM decision to decline (crypto/US stock/non-VND forex). No
            # fallback classify — that would re-map the foreign subject onto a VN intent.
            _trace("out_of_scope")
            return RouteResult(
                type="text",
                text=tc.input.get("text", "").strip(),
                reason=tc.input.get("reason", "out_of_scope"),
            )
        elif tc.name == "direct_reply":
            # Verify with classifier — LLM sometimes misuses direct_reply for financial queries.
            r = _fallback_classify(query, history)
            if r:
                _trace(r.get("type", "agent"), intent=r.get("intent", ""), ticker=r.get("ticker", ""), fallback_used=True)
                return r
            _trace("text")
            return RouteResult(type="text", text=tc.input.get("text", "").strip(), reason=tc.input.get("reason", ""))

    # No (or invalid) tool call — LLM bypassed tools; re-classify with history so an
    # implicit follow-up still resolves its subject. Prevents hallucinated "I don't have
    # data" answers for financial queries.
    r = _fallback_classify(query, history)
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
