"""
agents/conversation_router.py — LLM-on-top routing for all conversation turns.

Single LLM call at the start of each turn forces an explicit structured decision:
  - Call needs_agent_run(intent, ticker, query) → invoke agent pipeline
  - Call direct_reply(text)                     → stream response, skip agent

Both paths are tool calls — no free-form text escape hatch.
LLM cannot "answer from context"; it must explicitly pick a tool.
Fail-safe: no tool call → default to needs_agent_run (agent run, not direct reply).
"""

from __future__ import annotations

from typing import TypedDict

from agents.classifier import INTENTS

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
                "enum": [i for i in INTENTS if i != "conversation"],
                "description": (
                    "The financial intent that should handle this query. "
                    "Use 'macro_sector' for sector-wide or market-index queries "
                    "(e.g., banking sector, construction sector, VN30 index, sector ETF). "
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
            }
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


def llm_route(
    query: str,
    history: list[dict],
    system_prompt: str,
    client=None,
    max_tokens: int = 2048,
) -> RouteResult:
    """Single LLM call that decides: run agent tool OR reply directly.

    Both choices are explicit tool calls — no free-form text path.
    Fail-safe: if no tool called, defaults to agent run.

    Args:
        query:         Current user message.
        history:       Last N turns [{role, content}] from Postgres.
        system_prompt: Full assistant persona (from _build_system).
        client:        LLM client (create_client() if None).
        max_tokens:    Token budget for direct_reply responses.

    Returns:
        RouteResult with type="agent" (needs_agent_run called) or type="text" (direct_reply called).
    """
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
            tools=[AGENT_RUN_TOOL, DIRECT_REPLY_TOOL],
            max_tokens=max_tokens,
            temperature=0,
        )
        if resp.tool_calls:
            tc = resp.tool_calls[0]
            if tc.name == "needs_agent_run":
                inp = tc.input
                intent = inp.get("intent", "")
                if intent in INTENTS and intent != "conversation":
                    return RouteResult(
                        type="agent",
                        intent=intent,
                        ticker=(inp.get("ticker") or "").strip().upper(),
                        query=inp.get("query") or query,
                    )
            elif tc.name == "direct_reply":
                # Verify with classifier — LLM sometimes misuses direct_reply for financial queries.
                try:
                    from agents.classifier import classify_hybrid
                    r = classify_hybrid(query)
                    if r.intent and r.intent != "conversation":
                        return RouteResult(
                            type="agent",
                            intent=r.intent,
                            ticker=(r.ticker or "").strip().upper(),
                            query=query,
                        )
                except Exception:
                    pass
                return RouteResult(type="text", text=tc.input.get("text", "").strip())
        # No tool call — LLM bypassed tools; fail-safe: classify and route to agent.
        # This prevents LLM hallucinating "I don't have data" answers for financial queries.
        try:
            from agents.classifier import classify_hybrid
            r = classify_hybrid(query)
            if r.intent and r.intent != "conversation":
                return RouteResult(
                    type="agent",
                    intent=r.intent,
                    ticker=(r.ticker or "").strip().upper(),
                    query=query,
                )
        except Exception:
            pass
        # Genuine conversational turn (classifier says "conversation") or classifier failed
        text = resp.text.strip()
        if text:
            return RouteResult(type="text", text=text)
        return RouteResult(type="agent", intent="market_brief", query=query)
    except Exception:
        return RouteResult(type="agent", intent="market_brief", query=query)
