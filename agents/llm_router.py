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
    "fundamentals",
    "macro_sector",
    "news_sentiment",
    "investment_case",
    "screening",
    "qa_document",
    "market_brief",
    "conversation",
)

_SYSTEM = """\
You are a query intent classifier for a Vietnamese financial analysis assistant.

Classify the user's message into exactly one intent:

  price_action        — current price, foreign flow, volume, active buy/sell pressure
  technical_analysis  — RSI, MACD, moving averages, support/resistance, chart patterns, trend
  fundamentals        — P/E, P/B, ROE, EPS, revenue, profit, balance sheet, valuation vs peers
  macro_sector        — FX rates, oil/steel/commodity prices, interest rates, sector overview
  news_sentiment      — news, community sentiment, analyst commentary, market buzz around a stock
  investment_case     — buy/sell/hold recommendation, bull/bear thesis, comprehensive analysis
  screening           — filter or rank stocks by a criterion across many tickers
  qa_document         — specific factual question about a company's financial report content
  market_brief        — overall market overview: VNINDEX/VN30 breadth, gainers/losers, session recap
  conversation        — general chat, greeting, or clearly non-financial question

Rules:
- A ticker alone with no other signal → technical_analysis
- "should I buy / worth buying / good investment?" → investment_case
- "what is [metric]?" about a specific company → fundamentals or qa_document
- English or mixed-language queries follow the same rules — look at meaning, not language

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
                "description": "Stock ticker symbol if present (e.g. HPG, VNM, FPT). Omit if none.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the classification.",
            },
        },
        "required": ["intent", "reason"],
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
        text = resp.text.strip().lower()
        for intent in INTENTS:
            if intent in text:
                return RouterResult(intent=intent, ticker=None, reason="llm:text_scan")

    except Exception:
        pass

    return None
