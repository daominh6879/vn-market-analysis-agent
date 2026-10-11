"""
agents/intent_rules.py — deterministic intent overrides applied on top of the LLM router.

The LLM router/classifier is non-deterministic and mis-routed several question shapes
(market-wide questions → market_brief, "RSI của X" → no data, "cổ tức" → out-of-scope SQL).
These rules look only at the raw question (+ already-extracted tickers) and never call
an LLM or the DB.

  detect_market_topics(q)   → ["movers", "index", "foreign", "liquidity", "marketcap", "list"]
  override_intent(q, intent, tickers, screening) → (intent, screening, reason)
"""

from __future__ import annotations

import re

# ── market-wide topics (no single ticker) ─────────────────────────────────────

_TOPIC_PATTERNS: dict[str, re.Pattern] = {
    "movers": re.compile(
        r"(mã|cổ phiếu|cp)\s+nào.*(tăng|giảm)|(tăng|giảm)\s+(mạnh|nhiều|sâu)\s+nhất"
        r"|top\s*\d*\s*(tăng|giảm)|tăng trần|giảm sàn|(tăng|giảm)\s+giá\s+(mạnh|nhiều)"
        r"|\btop (gainers?|losers?)\b|biggest (gainers?|losers?)",
        re.IGNORECASE,
    ),
    "index": re.compile(
        r"(vn-?index|vn30|hnx-?index|hnx30|upcom).*(đóng cửa|bao nhiêu|mấy điểm|điểm|hôm nay|phiên)"
        r"|(đóng cửa|bao nhiêu điểm).*(vn-?index|vn30|hnx|upcom)",
        re.IGNORECASE,
    ),
    "foreign": re.compile(
        r"khối ngoại|nước ngoài|nhà đầu tư ngoại|foreign (investors?|flows?)",
        re.IGNORECASE,
    ),
    "liquidity": re.compile(
        r"thanh khoản\s+(cao|lớn)\s+nhất|giá trị giao dịch\s+(cao|lớn)\s+nhất"
        r"|khớp lệnh\s+nhiều\s+nhất|top\s*\d*\s*thanh khoản|giao dịch\s+(sôi động|mạnh)\s+nhất",
        re.IGNORECASE,
    ),
    "marketcap": re.compile(r"vốn hóa|vốn hoá|market cap", re.IGNORECASE),
    "list": re.compile(
        r"(liệt kê|danh sách|kể tên|những mã nào|các mã nào|có những mã)", re.IGNORECASE
    ),
}

# A "foreign" mention only counts as market-wide when it asks for a ranking / the whole market.
_FOREIGN_RANK_RE = re.compile(
    r"mã nào|cổ phiếu nào|top|nhiều nhất|mạnh nhất|toàn thị trường|thị trường|tổng|hôm nay|phiên",
    re.IGNORECASE,
)

_TECH_RE = re.compile(
    r"\b(rsi|macd|bollinger|ichimoku|fibonacci|fibo|obv|adx|stochastic|sma|ema)\b|\bma\s?\d{2,3}\b"
    r"|đường trung bình|hỗ trợ|kháng cự|quá mua|quá bán|chỉ báo",
    re.IGNORECASE,
)
_SCREEN_HINT_RE = re.compile(r"lọc|filter|screen|sàng lọc|mã nào|cổ phiếu nào", re.IGNORECASE)

_HISTORY_RE = re.compile(
    r"\d+\s*phiên|lịch sử giá|diễn biến giá|hôm qua.*hôm nay|so với hôm qua|tuần qua|tháng qua",
    re.IGNORECASE,
)
_DIVIDEND_RE = re.compile(r"cổ tức|dividend|chia cổ phiếu|cổ phiếu thưởng|quyền mua|chốt quyền", re.IGNORECASE)
_QUARTER_RE = re.compile(r"\bquý\b|\bq[1-4]\b", re.IGNORECASE)
_FIN_ITEM_RE = re.compile(r"doanh thu|lợi nhuận|lnst|lãi ròng|tổng tài sản|nợ phải trả|dòng tiền", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_YEARLY_METRIC_RE = re.compile(r"\b(roe|roa|eps|biên lợi nhuận)\b", re.IGNORECASE)

_COMMODITY_RE = re.compile(r"giá vàng|vàng sjc|\bsjc\b|giá dầu|dầu brent|dầu wti|\bgold price\b", re.IGNORECASE)
_CRYPTO_RE = re.compile(r"bitcoin|\bbtc\b|ethereum|\beth\b|crypto|tiền mã hoá|tiền mã hóa", re.IGNORECASE)

_VOLUME_SPIKE_RE = re.compile(
    r"(khối lượng|thanh khoản|volume|dòng tiền)\s+(đột biến|tăng đột biến|bất thường|vọt)"
    r"|đột biến\s+(khối lượng|thanh khoản|volume)",
    re.IGNORECASE,
)


def detect_market_topics(query: str, tickers: list[str] | None = None) -> list[str]:
    """Market-wide topics asked by the question. Empty when it names a specific stock.

    "list" is allowed alongside tickers-free sector asks only; ranking topics require no ticker.
    """
    q = query or ""
    topics = [name for name, pat in _TOPIC_PATTERNS.items() if pat.search(q)]
    if "foreign" in topics and not _FOREIGN_RANK_RE.search(q):
        topics.remove("foreign")
    if tickers:
        # "Khối ngoại mua bán HPG" is single-stock price_action, not a market ranking.
        return []
    # A volume-spike question is a screening question, not a ranking topic.
    if _VOLUME_SPIKE_RE.search(q) and "movers" in topics:
        topics.remove("movers")
    return topics


def is_volume_spike_query(query: str) -> bool:
    return bool(_VOLUME_SPIKE_RE.search(query or ""))


def override_intent(
    query: str,
    intent: str,
    tickers: list[str] | None = None,
    screening: list | None = None,
) -> tuple[str, list | None, str]:
    """Return (intent, screening, reason). reason == "" when nothing was overridden."""
    q = query or ""
    tickers = tickers or []

    # Gold / SJC / oil prices are covered by macro_sector (get_vn_gold, commodities) even when
    # the LLM refused them as "not a stock" - same data the market brief shows.
    if _COMMODITY_RE.search(q) and not tickers and not _CRYPTO_RE.search(q):
        return "macro_sector", None, "rule:commodity→macro_sector"

    if intent in ("out_of_scope", "conversation"):
        return intent, screening, ""

    # 1. Volume spike across the market/sector → screening with a default volume filter.
    if is_volume_spike_query(q) and not tickers:
        flt = screening or [{"indicator": "volume", "op": ">", "threshold": 1.5}]
        return "screening", flt, "rule:volume_spike→screening"

    # 2. Explicit filter verbs keep screening untouched.
    if re.search(r"lọc|filter|screen|sàng lọc", q, re.IGNORECASE):
        return "screening", screening, ""

    # 3. Market-wide questions that the LLM sends to market_brief.
    topics = detect_market_topics(q, tickers)
    # A bare number ("top 5", "10 mã") is NOT a screening threshold — only "<30", "dưới 20"...
    numeric_screen = intent == "screening" and (
        bool(screening) or bool(re.search(r"[<>]=?\s*\d|(dưới|trên|lớn hơn|nhỏ hơn|thấp hơn|cao hơn)\s*\d", q, re.IGNORECASE))
    )
    if topics and not numeric_screen and intent in (
        "market_brief", "macro_sector", "screening", "price_action", "rag_qa",
        "valuation", "investment_case", "technical_analysis",
    ):
        wants_brief = re.search(r"bản tin|tổng quan|đọc gì|nhận định|tổng kết", q, re.IGNORECASE)
        if not wants_brief:
            return "market_data", None, f"rule:market_data({','.join(topics)})"

    if len(tickers) == 1:
        # 4. Indicator question about one stock → full technical gather (RSI/MACD/MA...).
        if _TECH_RE.search(q) and not _SCREEN_HINT_RE.search(q) and intent in (
            "price_action", "rag_qa", "valuation", "macro_sector", "market_brief", "screening",
        ):
            return "technical_analysis", None, "rule:indicator→technical"
        # 5. Price history / N sessions → price_action (adds the session table).
        if _HISTORY_RE.search(q) and intent in ("rag_qa", "technical_analysis", "macro_sector"):
            return "price_action", None, "rule:history→price_action"

    if tickers:
        # 6. Dividends, quarterly figures, per-year ROE/EPS → financial lookup (rag_qa gather).
        years = _YEAR_RE.findall(q)
        if _DIVIDEND_RE.search(q):
            return "rag_qa", None, "rule:dividend→rag_qa"
        if _TOPIC_PATTERNS["marketcap"].search(q) and intent in (
            "valuation", "macro_sector", "price_action", "rag_qa", "market_brief", "investment_case",
        ):
            return "rag_qa", None, "rule:marketcap→rag_qa"
        if _QUARTER_RE.search(q) and _FIN_ITEM_RE.search(q):
            return "rag_qa", None, "rule:quarter→rag_qa"
        if years and _YEARLY_METRIC_RE.search(q) and intent in ("valuation", "macro_sector", "price_action"):
            return "rag_qa", None, "rule:yearly_metric→rag_qa"

    return intent, screening, ""
