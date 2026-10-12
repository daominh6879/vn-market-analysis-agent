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
    screening: list | None = None  # [{indicator, op, threshold}, ...] for screening intents
    market_topics: list | None = None  # data blocks a market_data question needs (index, movers, ...)
    macro_topics: list | None = None  # data blocks a macro_sector question needs (fx, commodities, gold, sector)
    rag_topics: list | None = None  # extra blocks a rag_qa question needs (dividend, quarterly, yearly_metric, marketcap)
    risk_flags: list | None = None  # advice / personal / prediction — drives disclaimers
    language: str | None = None  # "vi" | "en" — reply language


# ── routing decisions shared with agents/conversation_router.py ───────────────
# The LLM decides all of these; no keyword/regex layer overrides them downstream.

RAG_TOPICS = ("dividend", "quarterly", "yearly_metric", "marketcap")
RISK_FLAGS = ("advice", "personal", "prediction")
LANGUAGES = ("vi", "en")

RAG_TOPICS_SCHEMA = {
    "type": "array",
    "description": (
        "Only when intent='rag_qa' about ONE stock: extra data blocks the question needs, chosen by meaning. "
        "dividend = cổ tức / chia cổ phiếu / quyền mua; quarterly = revenue or profit of a specific quarter; "
        "yearly_metric = ROE/ROA/EPS/margin for specific years; marketcap = that stock's market capitalisation. "
        "Leave empty for plain financial-report questions."
    ),
    "items": {"type": "string", "enum": list(RAG_TOPICS)},
}
RISK_FLAGS_SCHEMA = {
    "type": "array",
    "description": (
        "Risk markers in the question. advice = asks whether to buy/sell/hold/cut loss/take profit; "
        "personal = commits all savings, borrowed money, margin or leverage (all-in); "
        "prediction = asks for a future price, forecast, target price, tomorrow/next week. Empty when none."
    ),
    "items": {"type": "string", "enum": list(RISK_FLAGS)},
}
LANGUAGE_SCHEMA = {
    "type": "string",
    "enum": list(LANGUAGES),
    "description": "Language the user wrote the question in; the reply follows it.",
}

# Rules that used to live in keyword/regex overrides. Appended to the classifier system prompt
# and the router's needs_agent_run description.
ROUTING_RULES = (
    "- A single stock's indicator question (RSI, MACD, MA, Bollinger, support/resistance, overbought/oversold) → technical_analysis, never screening.\n"
    "- A single stock's price over the last N sessions ('5 phiên gần nhất', 'hôm qua so với hôm nay', 'tuần qua') → price_action.\n"
    "- Dividends, a specific quarter's revenue/profit, a metric for a SPECIFIC year or period ('ROE của HPG năm 2024'), or ONE stock's market cap → rag_qa with the matching rag_topics. "
    "The same metric with NO period ('ROE của HPG', 'P/E của VCB', current EPS) → valuation.\n"
    "- 'Analyze a company/ticker' ('phân tích HPG') with no specific indicator or metric → investment_case (comprehensive view); an indicator question stays technical_analysis.\n"
    "- A market-wide question with no single stock (top gainers/losers, index level, foreign net buy/sell ranking, top liquidity, top market cap, list of a sector's tickers) → market_data with market_topics, even when worded loosely. "
    "Only the full newsletter ('bản tin', 'đọc gì trước giờ mở cửa') is market_brief. A ranking with no numeric threshold is NOT screening.\n"
    "- Unusual volume across the market or a sector ('khối lượng đột biến') → screening with the filter {indicator: volume, op: >, threshold: 1.5}.\n"
    "- End-of-session market commentary ('nhận định cuối phiên', 'tổng kết phiên', 'thị trường chiều nay') → closing_commentary.\n"
    "- Domestic gold (SJC), oil and commodity prices are IN scope → macro_sector (macro_topics gold/commodities), never out_of_scope. Crypto (Bitcoin, Ethereum) stays out_of_scope.\n"
    "- A question about a market index as a whole (VN-Index, VN30, HNX), including 'có nên mua không' or an outlook, is market_brief — investment_case is for a single stock only.\n"
    "- Always fill risk_flags and language from the question itself."
)


def clean_enum_list(values, allowed) -> list[str]:
    """Keep only strings in `allowed`, preserving order and dropping duplicates."""
    out: list[str] = []
    for v in values or []:
        if isinstance(v, str) and v in allowed and v not in out:
            out.append(v)
    return out


def clean_language(value) -> str:
    return value if value in LANGUAGES else "vi"


# ── intents ───────────────────────────────────────────────────────────────────

INTENTS = (
    "price_action", "technical_analysis", "rag_qa", "valuation", "macro_sector",
    "news_sentiment", "investment_case", "screening", "market_brief",
    "breakout_scan", "market_data", "closing_commentary", "conversation", "out_of_scope",
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
Classify based solely on what the current query is asking. A pure social turn (greeting, thanks, acknowledgement) is `conversation`; any other request with no Vietnamese-equity subject (crypto, foreign stocks, poems, code, weather...) is `out_of_scope`.
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
  market_brief        — a full pre-market / session-recap NEWSLETTER only ("bản tin thị trường", "tổng quan thị trường hôm nay", "đọc gì trước giờ mở cửa")
  market_data         — a SPECIFIC market-wide factual question: top gainers/losers ("mã nào tăng mạnh nhất"), index close ("VN-Index đóng cửa bao nhiêu điểm"), top foreign net buy/sell, top liquidity, top market cap, "liệt kê các mã trong ngành X". NOT a newsletter.
  closing_commentary  — end-of-session market commentary ("nhận định cuối phiên", "nhận định thị trường chiều nay", "tổng kết phiên hôm nay và kịch bản phiên sau"): index, liquidity, breadth, leading sectors, foreign flow and next-session levels in one short note. NOT the full newsletter, NOT a single fact.
  breakout_scan       — scan for stocks breaking out of a base or making new highs
  conversation        — ONLY greeting / thanks / acknowledgement / "what can you do", or unintelligible input (random characters, no request to decline). Requests to write poems, stories, code, translate, weather etc. are NOT financial → out_of_scope
  out_of_scope        — crypto (Bitcoin/Ethereum), US/foreign stocks, non-VND forex (EUR/USD, USD/JPY — no VND leg), or any non-financial task (poems, stories, code, translation, weather) — outside the Vietnamese equity universe

Rules:
- A ticker alone with no other signal → technical_analysis
- A query asking to analyze a company or ticker without naming an indicator or metric ("phân tích HPG") → investment_case
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
- A query that FILTERS/SELECTS stocks by a criterion ("lọc", "lọc cổ phiếu", "filter",
  "screen", "sàng lọc") → `screening`, even when the criterion is a technical indicator
  (RSI, MACD) — "lọc RSI < 30" is screening, NOT technical_analysis.
- For `screening` intent that names numeric filters (e.g. "RSI dưới 30", "ROE > 20%",
  "P/E < 10", "ROE > 20 AND RSI < 30"), set the `screening` ARRAY with one object per
  filter {indicator, op, threshold} — multiple filters are AND-ed. Do not leave it out.

Call the classify_intent tool."""

_SYSTEM += "\n\nMore routing rules (decide by meaning, not by keywords):\n" + ROUTING_RULES

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
                    "Use 'market_brief' ONLY for the full newsletter/session recap. "
                    "Use 'closing_commentary' for an end-of-session market commentary ('nhận định cuối phiên', 'tổng kết phiên hôm nay'). "
                    "Use 'market_data' for a specific market-wide fact (top gainers/losers, index close, foreign net buy/sell, top market cap, list tickers of a sector). "
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
            "market_topics": {
                "type": "array",
                "description": (
                    "Only when intent='market_data': EVERY data block the question needs, chosen by meaning (not by keywords). index = an index's close/level (VN-Index, VN30, HNX, UPCOM); movers = top gainers/losers; foreign = foreign net buy/sell; liquidity = top traded value; marketcap = top market cap; list = tickers of a sector. A question about 'VN-Index hôm nay thế nào' needs index; add foreign/movers only if also asked."
                ),
                "items": {"type": "string", "enum": ["index", "movers", "foreign", "liquidity", "marketcap", "list"]},
            },
            "macro_topics": {
                "type": "array",
                "description": (
                    "Only when intent='macro_sector': EVERY data block the question needs, chosen by meaning. fx = USD/VND central/bank exchange rate; commodities = oil, metals, world commodity prices; gold = domestic SJC gold price; sector = industry-group performance. 'Tỷ giá trung tâm hôm nay' needs only fx. Leave empty when the question is a broad macro/industry analysis (all blocks are fetched)."
                ),
                "items": {"type": "string", "enum": ["fx", "commodities", "gold", "sector"]},
            },
            "rag_topics": RAG_TOPICS_SCHEMA,
            "risk_flags": RISK_FLAGS_SCHEMA,
            "language": LANGUAGE_SCHEMA,
            "screening": {
                "type": "array",
                "description": (
                    "REQUIRED when intent='screening' AND the query names one or more numeric "
                    "filters (e.g. 'RSI dưới 30', 'ROE > 20%', 'P/E < 10', 'ROE > 20 AND RSI < 30'). "
                    "One object per filter; multiple filters are AND-ed. Otherwise omit. "
                    "indicator is the lower-case metric code — ratio: 'pe', 'pb', 'roe', "
                    "'roa', 'eps', 'ev_ebitda', 'de', 'gross_margin', 'net_margin', "
                    "'revenue_growth', 'earnings_growth'; absolute (tỷ VND): 'revenue', "
                    "'profit', 'market_cap' (vốn hóa); technical: 'rsi', 'macd', 'volume', 'adx', 'ma20', 'ma50', "
                    "'ma200' (giá so MA, %), 'pct_52w_high', 'pct_52w_low' (%). "
                    "op is the comparison, threshold the number."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "indicator": {"type": "string"},
                        "op": {"type": "string", "enum": ["<", ">", "<=", ">="]},
                        "threshold": {"type": "number"},
                    },
                    "required": ["indicator", "op", "threshold"],
                },
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

        system = _SYSTEM
        from core.tickers import _load_sector_labels
        sector_labels = sorted(_load_sector_labels())
        if sector_labels:
            system += (
                "\n\nAvailable sector/industry labels (when routing macro_sector/screening, "
                "use these exact names): " + ", ".join(sector_labels) + "."
            )

        resp = client.generate(
            messages=history_msgs,
            system=system,
            tools=[_TOOL],
            max_tokens=384,
            temperature=0,
        )

        if resp.tool_calls:
            tc = resp.tool_calls[0]
            intent = tc.input.get("intent", "conversation")
            ticker = tc.input.get("ticker") or None
            reason = tc.input.get("reason", "llm classification")
            screening = tc.input.get("screening") or None
            market_topics = [t for t in (tc.input.get("market_topics") or []) if isinstance(t, str)] or None
            macro_topics = [t for t in (tc.input.get("macro_topics") or []) if isinstance(t, str)] or None
            if intent not in INTENTS:
                intent = "conversation"
            if ticker and not ticker.strip():
                ticker = None
            return RouterResult(intent=intent, ticker=ticker, reason=f"llm:{reason}", screening=screening,
                                market_topics=market_topics, macro_topics=macro_topics,
                                rag_topics=clean_enum_list(tc.input.get("rag_topics"), RAG_TOPICS) or None,
                                risk_flags=clean_enum_list(tc.input.get("risk_flags"), RISK_FLAGS) or None,
                                language=clean_language(tc.input.get("language")))

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

    Always calls the LLM. Falls back to `conversation` on failure.
    """
    result = llm_classify(query, client=client, messages=messages)
    return result or RouterResult("conversation", None, "llm:fallback")
