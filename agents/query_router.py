"""
agents/query_router.py — Keyword-based intent router.

classify(query) → RouterResult

Intent labels (maps to 6 nhóm in stock-agent.md):
  investment_case     — nhóm 6: tổng hợp khuyến nghị MUA/BÁN/NẮM GIỮ/TÍCH LŨY
  price_action        — nhóm 1: giá hiện tại, dòng tiền, khối ngoại, volume
  technical_analysis  — nhóm 2: RSI, MACD, MA, support/resistance, xu hướng kỹ thuật
  fundamentals        — nhóm 3: P/E, P/B, ROE, định giá, tài chính công ty
  macro_sector        — nhóm 4: tỷ giá, dầu thô, thép, ngành, vĩ mô
  news_sentiment      — nhóm 5: tin tức, sentiment, bình luận cộng đồng
  screening           — nhóm 7: lọc cổ phiếu, tìm cổ phiếu theo tiêu chí
  market_brief        — tổng quan thị trường (VNINDEX / "thị trường")
  conversation        — fallback chat

Rules:
  - No LLM call — pure keyword matching (<1ms).
  - Market indices (VNINDEX/VN30) → market_brief first.
  - Priority (high → low): market_brief > investment_case > screening > news_sentiment >
    macro_sector > fundamentals > technical_analysis > price_action > conversation
  - Ticker alone (no other keywords) → technical_analysis default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── keyword sets ──────────────────────────────────────────────────────────────

_MARKET_KW = frozenset({
    # Require compound phrases — bare "thị trường" is too broad (appears in macro/sector queries too)
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
    # English screening patterns (multi-stock queries)
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

# VN words that match ticker regex but aren't tickers
_VN_NOISE = frozenset({
    # Vietnamese stopwords
    "PHÂN", "TÍCH", "HÔM", "NAY", "TUẦN", "TỚI", "NGÀNH",
    "CỔ", "PHIẾU", "CHỈ", "SỐ", "VÀ", "CÁC", "NHỀ", "GIÁ",
    "THỊ", "TIN", "TỨC", "XEM", "CÓ", "CỦA", "CHO", "BỊ",
    "HỎI", "LOẠI", "NÀO", "NHƯ", "ĐỂ", "VỀ", "MUA", "BÁN",
    "THE", "AND", "FOR", "WITH",
    # Financial ratios (look like tickers but aren't)
    "ROE", "ROA", "EPS", "PE", "PB", "NPM", "GPM",
    "TOP", "SQL", "DB",
    # Technical indicators
    "RSI", "MACD", "EMA", "SMA", "MA", "ATR", "ADX", "OBV",
    "TEMA", "DEMA", "WMA", "CCI", "MFI", "PPO",
    # Currency & commodity codes — never tickers
    "USD", "VND", "EUR", "JPY", "GBP", "CNY", "THB", "SGD",
    "WTI", "HRC", "LME",
    # Common English words that match A-Z{2-5}
    "BUY", "SELL", "HOLD", "ETF", "IPO", "NAV",
})


# ── result ────────────────────────────────────────────────────────────────────

@dataclass
class RouterResult:
    intent: str   # price_action | technical_analysis | fundamentals | macro_sector |
                  # news_sentiment | screening | market_brief | conversation
    ticker: str | None
    reason: str


# ── classifier ────────────────────────────────────────────────────────────────

def classify(query: str) -> RouterResult:
    """Classify query intent in <1ms — no LLM call."""
    lower = query.lower()
    upper = query.upper()

    # 1. Market-level (VNINDEX looks like ticker — intercept first)
    if any(kw in lower for kw in _MARKET_KW):
        return RouterResult("market_brief", None, "market keyword")
    for idx in _MARKET_INDICES:
        if idx in upper:
            return RouterResult("market_brief", None, f"market index {idx}")

    # 2. Extract first plausible ticker from ORIGINAL string.
    # Real tickers (HPG, VNM) are ALL-CAPS by convention. VN words are lowercase → won't match.
    ticker: str | None = None
    for m in re.finditer(r"\b([A-Z]{2,5})\b", query):
        t = m.group(1)
        if t not in _VN_NOISE and t not in _MARKET_INDICES:
            ticker = t
            break

    # 3. Investment case (comprehensive recommendation) — before all single-domain intents
    if any(kw in lower for kw in _INVESTMENT_CASE_KW):
        return RouterResult("investment_case", ticker, "investment case keyword")

    # 4. Screening — must check before fundamentals (both share financial keywords)
    if any(kw in lower for kw in _SCREENING_KW):
        return RouterResult("screening", ticker, "screening keyword")

    # 5. News / Sentiment
    if any(kw in lower for kw in _NEWS_KW):
        return RouterResult("news_sentiment", ticker, "news/sentiment keyword")

    # 6. Fundamentals — check BEFORE macro when valuation keywords present.
    #    "VCB P/E so với ngành ngân hàng?" has both fundamentals ("p/e") AND macro
    #    ("ngành ngân hàng"). Valuation keywords are always company-specific → win.
    if any(kw in lower for kw in _FUNDAMENTALS_KW):
        return RouterResult("fundamentals", ticker, "fundamentals keyword")

    # 7. Macro / Sector (only if no valuation keywords)
    if any(kw in lower for kw in _MACRO_KW):
        return RouterResult("macro_sector", ticker, "macro/sector keyword")

    # 8. Technical analysis
    if any(kw in lower for kw in _TECHNICAL_KW):
        return RouterResult("technical_analysis", ticker, "technical keyword")

    # 9. Price action / money flow
    if any(kw in lower for kw in _PRICE_ACTION_KW):
        return RouterResult("price_action", ticker, "price action keyword")

    # 10. Ticker alone → default technical_analysis
    if ticker:
        return RouterResult("technical_analysis", ticker, f"ticker {ticker} default")

    # 11. Fallback
    return RouterResult("conversation", None, "no financial intent detected")


# ── Hybrid classifier ─────────────────────────────────────────────────────────

_LLM_FALLBACK_MIN_WORDS = 3


def classify_hybrid(query: str, client=None) -> RouterResult:
    """Keyword-first router with LLM fallback on uncertain 'conversation' results.

    Only fires LLM when:
      1. Keyword router returns 'conversation'
      2. Query has >= 3 words (skip pure greetings like "xin chào")

    LLM result is used only when it returns a non-conversation intent.
    Any LLM failure silently falls back to the keyword result.
    Pass client= to inject a mock in tests.
    """
    result = classify(query)

    # Two uncertain cases warrant LLM:
    # 1. No financial signal at all → 'conversation'
    # 2. Ticker found but no intent keyword → 'technical_analysis' via ticker-only default
    #    (e.g. "Is HPG worth buying?" → ticker HPG default → should be investment_case)
    _is_ticker_default = (
        result.intent == "technical_analysis"
        and result.reason.endswith("default")
    )
    if result.intent != "conversation" and not _is_ticker_default:
        return result
    if len(query.split()) < _LLM_FALLBACK_MIN_WORDS:
        return result
    try:
        from agents.llm_router import INTENTS as _LLM_INTENTS, llm_classify
        llm_result = llm_classify(query, client=client)
        if (
            llm_result
            and llm_result.intent != "conversation"
            and llm_result.intent in _LLM_INTENTS
        ):
            # Preserve ticker from keyword result when LLM did not extract one.
            # Keyword regex is more reliable for ALL-CAPS ticker detection.
            if llm_result.ticker is None and result.ticker is not None:
                return RouterResult(
                    intent=llm_result.intent,
                    ticker=result.ticker,
                    reason=llm_result.reason,
                )
            return llm_result
    except Exception:
        pass
    return result
