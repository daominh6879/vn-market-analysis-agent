"""
agents/classifier.py — Intent classifier for the graph (keyword + LLM fallback).

Internal to agents/graph.py — no external callers after cache moved into graph.

classify(query) → RouterResult          keyword-only, <1ms
classify_hybrid(query) → RouterResult   keyword-first + LLM fallback on uncertain results
llm_classify(query) → RouterResult|None LLM tool-call fallback (used by classify_hybrid)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── keyword sets ──────────────────────────────────────────────────────────────

_MARKET_KW = frozenset({
    "vn-index", "vnindex", "vn30", "vn100",
    "ttck", "toàn thị trường", "chứng khoán hôm nay",
    "thị trường hôm nay", "thị trường chung", "thị trường tuần",
    "thị trường mở cửa", "thị trường đóng cửa", "thị trường tuần này",
    "thị trường việt nam", "index hôm nay",
    "thị trường chứng khoán",
})

_MARKET_INDICES = frozenset({
    "VNINDEX", "VN-INDEX", "VN30", "VN100", "HOSE", "HNX30",
})

_INVESTMENT_CASE_KW = frozenset({
    "nên mua không", "có nên mua", "nên mua hay bán", "nên bán không",
    "có nên đầu tư", "khuyến nghị", "khuyến cáo",
    "mua bán nắm giữ", "buy sell hold",
    "tổng kết", "tổng hợp", "kết luận đầu tư",
    "investment case", "luận điểm", "bull case", "bear case",
    "đánh giá tổng thể", "nhìn tổng thể",
    "đánh giá ngắn gọn", "đánh giá sơ bộ", "đánh giá cổ phiếu",
    "đánh giá công ty", "đánh giá mã", "đánh giá về",
    "nhận xét về", "nhận xét ngắn", "review cổ phiếu",
    "có tiềm năng không", "đáng mua không", "đáng đầu tư không",
    "nắm giữ hay bán", "tích lũy hay bán",
    "phân tích toàn diện", "phân tích đầy đủ",
    "full analysis", "complete analysis",
    "rủi ro và cơ hội",
})

_SCREENING_KW = frozenset({
    "lọc cổ phiếu", "tìm cổ phiếu", "bộ lọc",
    "cổ phiếu nào", "mã nào", "danh sách cổ phiếu",
    "top 5 mã", "top 10 mã", "top mã",
    "cổ phiếu có rsi", "cổ phiếu có pe", "cổ phiếu có roe",
    "tích lũy ngành", "cổ phiếu ngành",
    "screen", "screener", "lọc theo",
    "tất cả mã", "toàn bộ mã", "nhiều mã",
    "which stocks", "what stocks", "find stocks", "list stocks",
    "stocks with highest", "stocks with lowest", "stocks have",
    "companies with highest", "companies with lowest",
    "highest roe", "highest roa", "highest eps",
    "top stocks", "best stocks", "rank stocks",
})

_NEWS_KW = frozenset({
    "tin tức", "tin mới", "tin về", "bản tin",
    "sentiment", "tâm lý", "bình luận", "cộng đồng",
    "fireant", "cafef", "báo", "truyền thông",
    "mentions", "nhắc đến", "diễn đàn",
    "bullish", "bearish", "thị trường nói gì",
})

_MACRO_KW = frozenset({
    "tỷ giá", "usd/vnd", "usd vnd", "đô la",
    "dầu thô", "dầu brent", "wti", "dầu",
    "thép hrc", "hrc", "quặng sắt",
    "giá heo", "baltic dry", "cước vận tải",
    "lạm phát", "lãi suất", "fed", "nhnn",
    "vĩ mô", "macro", "kinh tế",
    "crack spread", "biên lợi nhuận kỳ vọng",
    "ngành thép", "ngành dầu khí", "ngành bán lẻ",
    "ngành ngân hàng", "sector",
    "xuất khẩu", "nhập khẩu",
})

_FUNDAMENTALS_KW = frozenset({
    "p/e", "pe ratio", "pe ", " pe",
    "p/b", "pb ratio",
    "roe", "roa", "eps",
    "định giá", "định giá cổ phiếu",
    "doanh thu", "lợi nhuận", "tài sản",
    "biên lợi nhuận gộp", "biên lợi nhuận",
    "nợ vay", "d/e", "đòn bẩy",
    "bctc", "báo cáo tài chính", "kiểm toán",
    "tăng trưởng doanh thu", "tăng trưởng lợi nhuận",
    "quý 1", "quý 2", "quý 3", "quý 4", "năm tài chính",
    "hợp nhất", "riêng lẻ", "tổng tài sản",
    "lợi nhuận gộp", "lợi nhuận ròng", "ebitda",
    "cổ tức", "chi phí vốn",
    "vốn chủ sở hữu", "vốn chủ",
})

_TECHNICAL_KW = frozenset({
    "phân tích kỹ thuật", "kỹ thuật",
    "rsi", "macd", "ema", "sma", "bollinger",
    "ma20", "ma50", "ma200",
    "support", "resistance", "hỗ trợ", "kháng cự",
    "xu hướng", "trend", "tín hiệu kỹ thuật",
    "mô hình nến", "nến nhật", "candlestick",
    "đỉnh", "đáy", "pivot",
    "cutloss", "cắt lỗ",
    "breakout", "breakdown",
    "phân kỳ", "hội tụ",
})

_PRICE_ACTION_KW = frozenset({
    "giá hiện tại", "giá hôm nay", "giá đóng cửa",
    "dòng tiền", "khối ngoại", "foreign",
    "active buy", "active sell", "mua chủ động", "bán chủ động",
    "khối lượng", "volume", "thanh khoản",
    "mua ròng", "bán ròng", "net buy", "net sell",
    "đột biến khối lượng",
    "tự doanh",
    "giá cổ phiếu",
})

_BREAKOUT_KW = frozenset({
    "quét breakout", "scan breakout", "tìm breakout",
    "breakout scan", "cổ phiếu breakout", "mã breakout",
    "bứt phá", "cổ phiếu bứt phá", "tín hiệu bứt phá",
    "vượt đỉnh nền", "nền giá", "tích lũy nền",
    "phát hiện breakout", "kiểm tra breakout",
    "có breakout không", "breakout chưa",
    "short breakout", "mid breakout", "long breakout",
})

_COMPANY_NAME_MAP: dict[str, str] = {
    "ngan hang quan doi": "MBB", "quan doi": "MBB", "mb bank": "MBB",
    "ngan hang ngoai thuong": "VCB", "vietcombank": "VCB",
    "ngan hang dau tu": "BID", "bidv": "BID",
    "vietinbank": "CTG", "ngan hang cong thuong": "CTG",
    "techcombank": "TCB", "vpbank": "VPB", "ngan hang thinh vuong": "VPB",
    "acb": "ACB", "a chau": "ACB", "ngan hang a chau": "ACB",
    "sacombank": "STB", "mbbank": "MBB", "hdbank": "HDB",
    "lpbank": "LPB", "lien viet": "LPB", "seabank": "SSB",
    "ocb": "OCB", "ngan hang phuong dong": "OCB",
    "msb": "MSB", "maritime": "MSB", "ncb": "NVB",
    "hoa phat": "HPG", "hoa sen": "HSG", "nam kim": "NKG",
    "vinhomes": "VHM", "vingroup": "VIC", "novaland": "NVL",
    "khang dien": "KDH", "dat xanh": "DXG", "nam long": "NLG",
    "fpt": "FPT", "viettel": "VGI", "cmc": "CMG",
    "vinamilk": "VNM", "masan": "MSN", "sabeco": "SAB",
    "bia sai gon": "SAB", "habeco": "BHN",
    "vietnam airlines": "HVN", "vietjet": "VJC", "gemadept": "GMD",
    "pvn": "GAS", "petrovietnam gas": "GAS", "pv gas": "GAS",
    "pvgas": "GAS", "bsr": "BSR", "binh son": "BSR", "pv oil": "OIL",
    "bao viet": "BVH", "imexpharm": "IMP", "duoc hau giang": "DHG",
}

_VN_NOISE = frozenset({
    "PHÂN", "TÍCH", "HÔM", "NAY", "TUẦN", "TỚI", "NGÀNH",
    "CỔ", "PHIẾU", "CHỈ", "SỐ", "VÀ", "CÁC", "NHỀ", "GIÁ",
    "THỊ", "TIN", "TỨC", "XEM", "CÓ", "CỦA", "CHO", "BỊ",
    "HỎI", "LOẠI", "NÀO", "NHƯ", "ĐỂ", "VỀ", "MUA", "BÁN",
    "THE", "AND", "FOR", "WITH",
    "ROE", "ROA", "EPS", "PE", "PB", "NPM", "GPM",
    "TOP", "SQL", "DB",
    "RSI", "MACD", "EMA", "SMA", "MA", "ATR", "ADX", "OBV",
    "TEMA", "DEMA", "WMA", "CCI", "MFI", "PPO",
    "USD", "VND", "EUR", "JPY", "GBP", "CNY", "THB", "SGD",
    "WTI", "HRC", "LME",
    "BUY", "SELL", "HOLD", "ETF", "IPO", "NAV",
})


# ── result ────────────────────────────────────────────────────────────────────

@dataclass
class RouterResult:
    intent: str
    ticker: str | None
    reason: str


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_company_name(query_lower: str) -> str | None:
    for name in sorted(_COMPANY_NAME_MAP, key=len, reverse=True):
        if name in query_lower:
            return _COMPANY_NAME_MAP[name]
    return None


def _strip_diacritics(text: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ── keyword classifier ────────────────────────────────────────────────────────

def classify(query: str) -> RouterResult:
    """Classify query intent in <1ms — no LLM call."""
    lower = query.lower()
    upper = query.upper()

    if any(kw in lower for kw in _BREAKOUT_KW):
        _ticker: str | None = None
        for _m in re.finditer(r"\b([A-Z]{2,5})\b", query):
            _t = _m.group(1)
            if _t not in _VN_NOISE and _t not in _MARKET_INDICES:
                _ticker = _t
                break
        if _ticker is None:
            _ticker = _resolve_company_name(_strip_diacritics(query))
        return RouterResult("breakout_scan", _ticker, "breakout keyword")

    if any(kw in lower for kw in _MARKET_KW):
        return RouterResult("market_brief", None, "market keyword")
    for idx in _MARKET_INDICES:
        if idx in upper:
            return RouterResult("market_brief", None, f"market index {idx}")

    ticker: str | None = None
    for m in re.finditer(r"\b([A-Z]{2,5})\b", query):
        t = m.group(1)
        if t not in _VN_NOISE and t not in _MARKET_INDICES:
            ticker = t
            break
    if ticker is None:
        ticker = _resolve_company_name(_strip_diacritics(query))

    if any(kw in lower for kw in _INVESTMENT_CASE_KW):
        return RouterResult("investment_case", ticker, "investment case keyword")
    if any(kw in lower for kw in _SCREENING_KW):
        return RouterResult("screening", ticker, "screening keyword")
    if any(kw in lower for kw in _NEWS_KW):
        return RouterResult("news_sentiment", ticker, "news/sentiment keyword")
    if any(kw in lower for kw in _FUNDAMENTALS_KW):
        return RouterResult("rag_qa", ticker, "fundamentals keyword")
    if any(kw in lower for kw in _MACRO_KW):
        return RouterResult("macro_sector", ticker, "macro/sector keyword")
    if any(kw in lower for kw in _TECHNICAL_KW):
        return RouterResult("technical_analysis", ticker, "technical keyword")
    if any(kw in lower for kw in _PRICE_ACTION_KW):
        return RouterResult("price_action", ticker, "price action keyword")
    if ticker:
        return RouterResult("technical_analysis", ticker, f"ticker {ticker} default")
    return RouterResult("conversation", None, "no financial intent detected")


# ── LLM fallback ──────────────────────────────────────────────────────────────

INTENTS = (
    "price_action", "technical_analysis", "rag_qa", "macro_sector",
    "news_sentiment", "investment_case", "screening", "market_brief",
    "breakout_scan", "conversation",
)

_SYSTEM = """\
You are a query intent classifier for a Vietnamese financial analysis assistant.

When conversation history is provided, use it to resolve the ticker for ambiguous follow-up queries
(e.g. "phân tích hôm nay" after discussing TCB → ticker is TCB).
If the current query explicitly mentions a different ticker or asks about all stocks, use that context instead.

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


def llm_classify(query: str, client=None, messages: list | None = None) -> RouterResult | None:
    """Classify query via LLM tool-call. Returns None on any error.

    Pass `messages` (conversation history) so the LLM can resolve tickers from context
    instead of relying on post-hoc inheritance rules.
    """
    try:
        if client is None:
            from llm.factory import create_client
            client = create_client()

        from llm.types import Message

        # Build multi-turn history: last 6 user/assistant turns for context, then current query.
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

        import re as _re
        text = resp.text.strip()
        text_lower = text.lower()
        for intent in INTENTS:
            if intent in text_lower:
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


# ── hybrid classifier ─────────────────────────────────────────────────────────

_LLM_FALLBACK_MIN_WORDS = 3


def classify_hybrid(query: str, client=None, messages: list | None = None) -> RouterResult:
    """Keyword-first router with LLM fallback on uncertain results.

    Pass `messages` (conversation history) so LLM can resolve tickers from context.
    """
    result = classify(query)

    _is_ticker_default = (
        result.intent == "technical_analysis"
        and result.reason.endswith("default")
    )
    # Short queries (< 3 words): keyword result is sufficient — LLM adds no value.
    if len(query.split()) < _LLM_FALLBACK_MIN_WORDS:
        return result

    # LLM fallback when intent or ticker is uncertain on a multi-word query.
    _needs_llm = result.intent == "conversation" or _is_ticker_default or not result.ticker
    if not _needs_llm:
        return result
    try:
        llm_result = llm_classify(query, client=client, messages=messages)
        if llm_result and llm_result.intent in INTENTS:
            return llm_result
    except Exception:
        pass
    return result
