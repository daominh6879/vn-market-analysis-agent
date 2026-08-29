"""
agents/query_router.py — Keyword-based intent router for conversation queries.

classify(query) → RouterResult

Intent labels:
  ticker_analysis  — single ticker price/technical analysis → agents/graph.py
  market_brief     — full market overview → agents/market_brief_graph.py
  qa_document      — HPG financial document / SQL query → rag pipeline
  conversation     — general chat fallback → LLM direct

Rules:
  - No LLM call — pure keyword matching for speed (<1ms).
  - Market indices (VNINDEX/VN30) → market_brief, not ticker_analysis.
  - Ticker alone without analysis keywords → ticker_analysis (default).
  - Financial statement keywords (doanh thu, lợi nhuận…) → qa_document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── keyword sets ──────────────────────────────────────────────────────────────

_MARKET_KW = frozenset({
    "thị trường", "vn-index", "vnindex", "vn30", "vn100",
    "ttck", "toàn thị trường", "chứng khoán hôm nay",
    "thị trường hôm nay", "thị trường chung", "thị trường tuần",
    "thị trường mở cửa", "thị trường đóng cửa", "thị trường tuần này",
    "thị trường việt nam", "index hôm nay",
})

_MARKET_INDICES = frozenset({
    "VNINDEX", "VN-INDEX", "VN30", "VN100", "HOSE", "HNX30",
})

_ANALYSIS_KW = frozenset({
    "phân tích", "giá hôm nay", "kỹ thuật", "chỉ số kỹ thuật",
    "rsi", "macd", "ema", "sma", "bollinger",
    "hôm nay", "tuần này", "biến động", "xu hướng",
    "tín hiệu", "support", "resistance", "tin tức",
    "phân tích kỹ thuật", "phân tích cổ phiếu",
    "giá cổ phiếu", "diễn biến giá",
})

_DOC_KW = frozenset({
    "doanh thu", "lợi nhuận", "tài sản", "công ty con", "nhân viên",
    "kế toán", "chi phí", "dòng tiền", "vốn chủ sở hữu", "vốn chủ",
    "bctc", "báo cáo tài chính", "kiểm toán", "nợ phải trả",
    "cổ tức", "eps", "roe", "roa", "pe ratio", "p/b",
    "quý 1", "quý 2", "quý 3", "quý 4", "năm tài chính",
    "hợp nhất", "riêng lẻ", "tổng tài sản", "doanh số",
    "lợi nhuận gộp", "lợi nhuận ròng", "ebitda",
    "top 5", "top 10", "xếp hạng", "cao nhất", "thấp nhất",  # SQL aggregation
})

# VN words that match ticker regex but aren't tickers
_VN_NOISE = frozenset({
    "PHÂN", "TÍCH", "HÔM", "NAY", "TUẦN", "TỚI", "NGÀNH",
    "CỔ", "PHIẾU", "CHỈ", "SỐ", "VÀ", "CÁC", "NHỀ", "GIÁ",
    "THỊ", "TIN", "TỨC", "XEM", "CÓ", "CỦA", "CHO", "BỊ",
    "HỎI", "LOẠI", "NÀO", "NHƯ", "ĐỂ", "VỀ", "MUA", "BÁN",
    "THE", "AND", "FOR", "WITH", "ROE", "ROA", "EPS",
})


# ── result ────────────────────────────────────────────────────────────────────

@dataclass
class RouterResult:
    intent: str           # ticker_analysis | market_brief | qa_document | conversation
    ticker: str | None    # for ticker_analysis / qa_document
    reason: str


# ── classifier ────────────────────────────────────────────────────────────────

def classify(query: str) -> RouterResult:
    """Classify query intent in <1ms — no LLM call."""
    lower = query.lower()
    upper = query.upper()

    # 1. Market-level check first (VNINDEX looks like a ticker — intercept it)
    if any(kw in lower for kw in _MARKET_KW):
        return RouterResult("market_brief", None, "market keyword")
    for idx in _MARKET_INDICES:
        if idx in upper:
            return RouterResult("market_brief", None, f"market index {idx}")

    # 2. Extract first plausible ticker from ORIGINAL string (not uppercased).
    # Real tickers (HPG, VNM, FPT) are written ALL-CAPS by users.
    # Vietnamese words (xin, bạn, doanh, thu) are lowercase → won't match.
    ticker: str | None = None
    for m in re.finditer(r"\b([A-Z]{2,5})\b", query):
        t = m.group(1)
        if t not in _VN_NOISE and t not in _MARKET_INDICES:
            ticker = t
            break

    # 3. Financial document / SQL keywords → qa_document
    if any(kw in lower for kw in _DOC_KW):
        return RouterResult("qa_document", ticker, "financial doc/SQL keyword")

    # 4. Ticker + analysis keyword → ticker_analysis
    if ticker and any(kw in lower for kw in _ANALYSIS_KW):
        return RouterResult("ticker_analysis", ticker, f"ticker {ticker} + analysis keyword")

    # 5. Ticker alone → ticker_analysis (default for any stock mention)
    if ticker:
        return RouterResult("ticker_analysis", ticker, f"ticker {ticker} mentioned")

    # 6. Fallback: conversational LLM
    return RouterResult("conversation", None, "no financial intent detected")
