"""
agents/llm_router.py — LLM-based fallback classifier for ambiguous queries.

Called only when keyword router returns 'conversation' on a non-trivial query.
Uses tool-call for structured output: {intent, ticker, reason}.
Returns None on any failure — caller must fall back to keyword result.
"""

from __future__ import annotations

from llm.types import Message
from agents.query_router import RouterResult

INTENTS = (
    "price_action",
    "technical_analysis",
    "rag_qa",
    "macro_sector",
    "news_sentiment",
    "investment_case",
    "screening",
    "market_brief",
    "conversation",
)

_SYSTEM = """\
You are a query intent classifier for a Vietnamese financial analysis assistant.

Classify the user's message into exactly one intent:

  price_action        — current price, foreign flow, volume, active buy/sell pressure
  technical_analysis  — RSI, MACD, moving averages, support/resistance, chart patterns, trend
  rag_qa              — P/E, P/B, ROE, EPS, revenue, profit, balance sheet, valuation, or any factual question about a company's financial report content
  macro_sector        — FX rates, oil/steel/commodity prices, interest rates, sector overview
  news_sentiment      — news, community sentiment, analyst commentary, market buzz around a stock
  investment_case     — buy/sell/hold recommendation, bull/bear thesis, comprehensive analysis
  screening           — filter or rank stocks by a criterion across many tickers
  market_brief        — overall market overview: VNINDEX/VN30 breadth, gainers/losers, session recap
  conversation        — general chat, greeting, or clearly non-financial question

Rules:
- A ticker alone with no other signal → technical_analysis
- "phân tích X" / "analyze X" / "phân tích X hôm nay" where X is a company or ticker → technical_analysis
- "should I buy / worth buying / good investment?" → investment_case
- "đánh giá [company/ticker]" / "nhận xét về [company]" / "review [ticker]" → investment_case
- "what is [metric]?" or "revenue/profit/P/E of X?" → rag_qa
- English or mixed-language queries follow the same rules — look at meaning, not language
- Time words ("hôm nay", "today", "tuần này") do NOT change intent — classify by the financial action, not the time
- If the query mentions a Vietnamese company by name (not ticker), use your knowledge to resolve it
  to its HOSE/HNX ticker symbol (e.g. "Agribank" → "AGB", "Ngân hàng Nông nghiệp..." → "AGB").
  Common examples: "Ngân hàng Quân đội" → "MBB", "Hòa Phát" → "HPG", "Hòa Sen" → "HSG",
  "Vinamilk" → "VNM", "Vietcombank" → "VCB", "Techcombank" → "TCB", "VPBank" → "VPB",
  "Masan" → "MSN", "Vinhomes" → "VHM", "Vingroup" → "VIC", "FPT" → "FPT".
  If you do not know the ticker, leave ticker empty and classify intent based on context.

Call the classify_intent tool."""

_TOOL = {
    "name": "classify_intent",
    "description": "Classify the user query intent for routing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": list(INTENTS),
                "description": "The routing intent label.",
            },
            "ticker": {
                "type": "string",
                "description": (
                    "Stock ticker symbol (2-5 uppercase letters). "
                    "If the query mentions a Vietnamese company by name, resolve it to its "
                    "HOSE/HNX ticker using your knowledge. "
                    "Use empty string if no company is mentioned or ticker is unknown."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the classification.",
            },
        },
        "required": ["intent", "ticker", "reason"],
    },
}


def llm_classify(query: str, client=None) -> RouterResult | None:
    """Classify query via LLM tool-call.

    Returns RouterResult on success, None on any error (caller falls back to keyword result).
    Pass client= to inject a mock in tests.
    """
    try:
        if client is None:
            from llm.factory import create_client
            client = create_client()

        resp = client.generate(
            messages=[Message(role="user", content=query)],
            system=_SYSTEM,
            tools=[_TOOL],
            max_tokens=256,
            temperature=0,
        )

        if resp.tool_calls:
            tc = resp.tool_calls[0]
            intent = tc.input.get("intent", "conversation")
            ticker = tc.input.get("ticker") or None
            reason = tc.input.get("reason", "llm classification")
            if intent not in INTENTS:
                intent = "conversation"
            # Normalise empty/whitespace ticker
            if ticker and not ticker.strip():
                ticker = None
            return RouterResult(
                intent=intent,
                ticker=ticker,
                reason=f"llm:{reason}",
            )

        # Graceful text-scan fallback when tool_calls absent
        import re as _re
        text = resp.text.strip()
        text_lower = text.lower()
        for intent in INTENTS:
            if intent in text_lower:
                # Try to extract ticker from LLM response text (e.g. "ticker: VPB" or bare "VPB")
                from agents.query_router import _VN_NOISE
                ticker_fallback = None
                for m in _re.finditer(r"\b([A-Z]{2,5})\b", text):
                    t = m.group(1)
                    if t not in _VN_NOISE and t not in ("INTENTS", "SSE"):
                        ticker_fallback = t
                        break
                return RouterResult(intent=intent, ticker=ticker_fallback, reason="llm:text_scan")

    except Exception:
        pass

    return None
