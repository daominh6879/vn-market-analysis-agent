"""
agents/classifier.py — Intent classifier for the graph (LLM-based).

Internal to agents/graph.py — no external callers after cache moved into graph.

classify_hybrid(query) → RouterResult   LLM classification with conversation history support
llm_classify(query)    → RouterResult|None  LLM tool-call (used by classify_hybrid)
"""

from __future__ import annotations

from dataclasses import dataclass


# ── result ────────────────────────────────────────────────────────────────────

@dataclass
class RouterResult:
    intent: str
    ticker: str | None
    reason: str


# ── intents ───────────────────────────────────────────────────────────────────

INTENTS = (
    "price_action", "technical_analysis", "rag_qa", "valuation", "macro_sector",
    "news_sentiment", "investment_case", "screening", "market_brief",
    "breakout_scan", "conversation", "out_of_scope",
)

# Fixed decline used when the classifier/router decides a subject is outside the VN
# universe (crypto, US/foreign stocks, non-VND forex). Shared by conversation_router
# fallback and the graph's out_of_scope node.
OUT_OF_SCOPE_REPLY = (
    "Xin lỗi, tôi chỉ hỗ trợ phân tích chứng khoán Việt Nam (HOSE/HNX/UPCOM). "
    "Tôi chưa hỗ trợ chứng khoán nước ngoài, tiền mã hoá (crypto) hay ngoại hối ngoài VND."
)


# ── LLM classifier ────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a query intent classifier for a Vietnamese financial analysis assistant.

When conversation history is provided, use it to resolve the ticker for ambiguous queries where
the current message does not name a company or ticker explicitly.
If the current query explicitly mentions a different ticker or asks about all stocks, use that context instead.

IMPORTANT: History is ONLY for resolving the ticker of the current query — never to change its intent.
Classify based solely on what the current query is asking. If it carries no financial intent, classify as `conversation`.
A separate routing step decides whether a `conversation` result should trigger an agent run — do not anticipate that here.

Classify the user's message into exactly one intent:

  price_action        — current price, foreign flow, volume, active buy/sell pressure
  technical_analysis  — RSI, MACD, moving averages, support/resistance, chart patterns, trend
  rag_qa              — factual questions about a company's financial report content: revenue, profit, balance sheet, cash flow, specific period figures (e.g. "doanh thu HPG 2024 là bao nhiêu")
  valuation           — valuation metrics and peer comparison: P/E, P/B, ROE, EPS, EV/EBITDA, comparing a stock's valuation against its sector peers (e.g. "VCB P/E so với ngành ngân hàng", "ROE của HPG"), or comparing two or more stocks against each other (e.g. "so sánh BID và CTG", "HPG vs VCB")
  macro_sector        — FX rates, oil/steel/commodity prices, interest rates, or a pure sector overview with NO specific stock valuation metric (P/E, P/B, ROE, EPS)
  news_sentiment      — news, community sentiment, analyst commentary, market buzz around a stock
  investment_case     — buy/sell/hold recommendation, bull/bear thesis, comprehensive analysis
  screening           — filter or rank stocks by a criterion across many tickers
  market_brief        — overall market overview: VNINDEX/VN30 breadth, gainers/losers, session recap
  breakout_scan       — scan for stocks breaking out of a base or making new highs
  conversation        — general chat, greeting, or clearly non-financial question
  out_of_scope        — crypto (Bitcoin/Ethereum), US/foreign stocks, or non-VND forex (EUR/USD, USD/JPY — no VND leg) — outside the Vietnamese equity universe

Rules:
- A ticker alone with no other signal → technical_analysis
- A query asking to analyze a company or ticker → technical_analysis
- A query asking for a buy/sell/hold recommendation or comprehensive evaluation → investment_case
- A query asking for a valuation metric (P/E, P/B, ROE, EPS, EV/EBITDA) of a company, or comparing it against its sector → valuation
- A query asking about financial-report content (revenue, profit, balance sheet, specific period figures) → rag_qa
- A query asking a stock's valuation metric (P/E, P/B, ROE, EPS) versus its sector → valuation, NOT macro_sector. A sector word ("ngành ngân hàng") does not override a metric keyword ("P/E").
- A query about crypto, a US/foreign stock, or non-VND forex (EUR/USD, USD/JPY — no VND leg) → out_of_scope (not any VN intent). USD/VND and other VND-pegged FX → macro_sector.
- A query comparing two or more tickers/companies (e.g. "so sánh BID và CTG", "HPG vs VCB", "giữa A, B") → valuation, even when no specific metric is named.
- English or mixed-language queries follow the same rules — look at meaning, not language
- Time words do NOT change intent — classify by the financial action, not the time
- If the query mentions a Vietnamese company by name (not ticker), use your knowledge of
  HOSE/HNX listed companies to resolve it to its ticker symbol.
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
                "description": (
                    "The routing intent label. "
                    "Use 'macro_sector' for sector-wide or index queries "
                    "(banking sector, construction sector, VN30, sector ETF) "
                    "with no specific stock valuation metric. "
                    "If the query names a stock and a valuation metric (P/E, P/B, ROE, EPS) "
                    "against the sector, classify as 'valuation', not 'macro_sector'. "
                    "Use 'valuation' for a stock's valuation metric (P/E, P/B, ROE, EPS), comparing it to sector peers, "
                    "or comparing two or more stocks against each other (so sánh A và B, A vs B). "
                    "Use 'rag_qa' for financial-report content (revenue, profit, balance sheet). "
                    "Use 'price_action' only for a single stock's price/volume action. "
                    "Use 'market_brief' for broad market overview (VNINDEX, HNX, overall session). "
                    "Use 'screening' for filter/scan queries. "
                    "Use 'investment_case' for buy/sell/hold on a specific stock. "
                    "Use 'out_of_scope' for crypto, US/foreign stocks, or non-VND forex "
                    "(EUR/USD, USD/JPY — no VND leg). USD/VND → macro_sector."
                ),
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


def llm_classify(query: str, client=None, messages: list | None = None) -> RouterResult | None:
    """Classify query via LLM tool-call. Returns None on any error."""
    try:
        if client is None:
            from llm.factory import create_client
            client = create_client()

        from llm.types import Message

        history_msgs: list[Message] = []
        if messages:
            for m in messages[-6:]:
                role = m.get("role", "")
                content = m.get("content", "")
                if role in ("user", "assistant") and content:
                    history_msgs.append(Message(role=role, content=str(content)[:400]))
        history_msgs.append(Message(role="user", content=query))

        resp = client.generate(
            messages=history_msgs,
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
            if ticker and not ticker.strip():
                ticker = None
            return RouterResult(intent=intent, ticker=ticker, reason=f"llm:{reason}")

        # Text-scan fallback when LLM skips tool call
        text_lower = resp.text.strip().lower()
        for intent in INTENTS:
            if intent in text_lower:
                return RouterResult(intent=intent, ticker=None, reason="llm:text_scan")

    except Exception:
        pass

    return None


def classify_hybrid(query: str, client=None, messages: list | None = None) -> RouterResult:
    """LLM-based classifier with conversation history support.

    Always calls LLM. Falls back to `conversation` on failure.
    """
    result = llm_classify(query, client=client, messages=messages)
    return result or RouterResult("conversation", None, "llm:fallback")
