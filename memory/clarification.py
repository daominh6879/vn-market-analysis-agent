"""
memory/clarification.py — Detect query ambiguity, generate clarification, merge on resume.

Ambiguity types (stored in pending_context.missing):
  "ticker"  — intent requires a ticker but none could be resolved
  "company" — company name mentioned but not resolvable to ticker
  "intent"  — query sounds financial but intent is unclear (conversation fallback)

Flow:
  Turn N (ambiguous):
    detect_ambiguity(route, query) → PendingContext
    build_clarification_message(pending) → str (sent to user)
    set_pending_context(conversation_id, pending)   [in conversation.py]

  Turn N+1 (user replies):
    pending = get_pending_context(conversation_id)  [in conversation.py]
    if pending: merged_query = merge_with_pending(pending, new_query)
    clear_pending_context(conversation_id)
    re-route merged_query
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class PendingContext:
    original_query: str
    missing: list[str]          # ["ticker"] | ["intent"] | ["company"] | combo
    intent: Optional[str]       # best-guess intent, may be None
    ticker: Optional[str]       # already-resolved ticker, may be None


# Intent → human-readable Vietnamese label for clarification messages
_INTENT_LABELS: dict[str, str] = {
    "price_action":       "theo dõi giá và dòng tiền",
    "technical_analysis": "phân tích kỹ thuật",
    "rag_qa":             "tra cứu tài chính",
    "macro_sector":       "phân tích vĩ mô / ngành",
    "news_sentiment":     "xem tin tức và sentiment",
    "investment_case":    "đánh giá đầu tư",
    "screening":          "lọc cổ phiếu",
    "market_brief":       "tổng quan thị trường",
}

# Intent choices offered when intent is unclear
_INTENT_CHOICES = (
    "phân tích kỹ thuật (RSI, MACD, xu hướng)",
    "đánh giá đầu tư (mua/bán/giữ)",
    "tin tức & sentiment",
    "tra cứu tài chính (P/E, doanh thu, lợi nhuận)",
    "giá & dòng tiền",
)

# Keywords that suggest a financial query even when router returned 'conversation'
_FINANCIAL_SIGNALS = frozenset({
    "cổ phiếu", "co phieu", "mã", "ticker", "ngân hàng", "ngan hang",
    "công ty", "cong ty", "doanh nghiệp", "doanh nghiep",
    "đầu tư", "dau tu", "phân tích", "phan tich", "đánh giá", "danh gia",
    "mua", "bán", "ban", "giá", "gia", "thị trường", "thi truong",
    "chứng khoán", "chung khoan", "invest", "stock", "share",
})

# Intents that require a resolved ticker
_TICKER_REQUIRED = frozenset({
    "price_action", "technical_analysis", "rag_qa",
    "news_sentiment", "investment_case",
})


def detect_ambiguity(route, query: str) -> Optional[PendingContext]:
    """Return PendingContext if the route result is ambiguous, else None.

    Three cases:
    1. Ticker-required intent but ticker=None → missing=["ticker"]
    2. Conversation fallback on financial-sounding query → missing=["intent"]
    3. Ticker-required intent, ticker=None, AND company name fragments present
       that the router could not resolve → missing=["company"]
       (distinguished from case 1 by presence of company-name-like text)
    """
    intent = route.intent
    ticker = route.ticker or None  # normalize "" → None
    lower = query.lower()

    # Case 0: bare ticker — user provided only a ticker symbol with no action expressed
    # Detected from query content, not classifier internals.
    _tokens = query.strip().split()
    _is_bare_ticker = (
        len(_tokens) <= 2
        and any(t.upper() == t and 2 <= len(t) <= 5 for t in _tokens)
        and not any(c in lower for c in ("phân tích", "giá", "tin", "mua", "bán", "đánh giá", "review"))
    )
    if intent in _TICKER_REQUIRED and ticker and _is_bare_ticker:
        return PendingContext(
            original_query=query,
            missing=["intent"],
            intent=intent,
            ticker=ticker,
        )

    # Case 1 / 3: ticker required but missing
    if intent in _TICKER_REQUIRED and ticker is None:
        # Heuristic: "company" when query contains noun-like entity words beyond
        # known financial action words.  Require >= 3 words AND at least one word
        # that is not a known financial verb/adjective fragment.
        _ACTION_WORDS = frozenset({
            "đánh", "giá", "phân", "tích", "xem", "hỏi", "cho",
            "biết", "nói", "về", "của", "ngắn", "gọn", "sơ", "bộ",
            "tổng", "hợp", "kết", "luận", "review", "analyze",
            "analysis", "evaluate", "brief", "quick",
        })
        words = query.split()
        non_action = [w for w in words if w.lower() not in _ACTION_WORDS]
        has_company_fragment = (
            len(words) >= 3
            and len(non_action) >= 2
            and not any(w.isupper() and 2 <= len(w) <= 5 for w in words)
        )
        missing = ["company"] if has_company_fragment else ["ticker"]
        return PendingContext(
            original_query=query,
            missing=missing,
            intent=intent,
            ticker=None,
        )

    # Case 2: conversation fallback but query sounds financial
    if intent == "conversation":
        if len(query.split()) >= 3 and any(sig in lower for sig in _FINANCIAL_SIGNALS):
            return PendingContext(
                original_query=query,
                missing=["intent"],
                intent=None,
                ticker=ticker,
            )

    return None


def build_clarification_message(pending: PendingContext) -> str:
    """Generate a clarification question for the user based on what's missing."""
    missing = pending.missing
    intent_label = _INTENT_LABELS.get(pending.intent or "", "phân tích") if pending.intent else "phân tích"

    if "company" in missing:
        return (
            f"Tôi không nhận ra tên công ty trong câu hỏi của bạn. "
            f"Bạn có thể cung cấp mã ticker không? "
            f"(ví dụ: HPG, VNM, MBB, VCB)"
        )

    if "ticker" in missing:
        return (
            f"Bạn muốn {intent_label} của mã cổ phiếu hoặc công ty nào? "
            f"(ví dụ: HPG, VNM, MBB)"
        )

    if "intent" in missing:
        choices = "\n".join(f"  • {c}" for c in _INTENT_CHOICES)
        ticker_part = f" về **{pending.ticker}**" if pending.ticker else ""
        return (
            f"Bạn muốn tôi làm gì{ticker_part}?\n{choices}\n\n"
            f"Hoặc mô tả cụ thể hơn câu hỏi của bạn."
        )

    return "Bạn có thể nói rõ hơn câu hỏi của bạn không?"


def merge_with_pending(pending: PendingContext, new_query: str) -> str:
    """Combine original query with user's clarification reply into one re-routable query.

    Strategy: concat original + new reply. The combined text has enough signal for
    the router (original intent keywords + newly provided ticker/company name).
    """
    original = pending.original_query.strip()
    new = new_query.strip()
    if not new:
        return original
    return f"{original} {new}"


def pending_to_dict(pending: PendingContext) -> dict:
    return asdict(pending)


def pending_from_dict(d: dict) -> PendingContext:
    return PendingContext(
        original_query=d.get("original_query", ""),
        missing=d.get("missing", []),
        intent=d.get("intent"),
        ticker=d.get("ticker"),
    )
