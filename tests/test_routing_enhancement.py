"""
tests/test_routing_enhancement.py — Golden-set routing eval + regression guards.

Covers the routing fixes in docs/plan-routing-enhancement.md (items #1-#11):

  Unit (fast, no LLM/network):
    - _route_after_clarify golden set — market_brief/simple/decompose/conversation
    - raw_tickers / extract_tickers — uppercase-first + stopword false-positive guard (1.4)
    - _validate_ticker — universe + market-index whitelist (1.10)
    - _fallback_classify — passes conversation history (1.9)
    - llm_route fail-safe — returns text error, never market_brief (1.8)
    - check_cache_node — ticker-less follow-up keys off self-contained query (1.3)

  E2E (real LLM, marked @pytest.mark.e2e):
    - llm_route golden set — query → expected intent + ticker (2.3)

Run unit only:
    pytest tests/test_routing_enhancement.py -v -m "not e2e"

Run everything (slow, real LLM):
    pytest tests/test_routing_enhancement.py -v -s -m e2e
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()

from agents.classifier import RouterResult
from core.tickers import raw_tickers, extract_tickers


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — _route_after_clarify golden set (1.2 conversation branch + dispatch)
# ═══════════════════════════════════════════════════════════════════════════════

# (intent, ticker) → expected route. Pure function, no LLM.
GOLDEN_ROUTES = [
    ("market_brief",     "",            "market_brief"),
    ("technical_analysis", "HPG",       "simple"),
    ("valuation",          "HPG",       "simple"),
    ("price_action",       "HPG",       "simple"),
    ("investment_case",    "HPG",       "decompose"),   # _COMPLEX_INTENTS
    ("macro_sector",       "",          "decompose"),   # complex + no ticker
    ("screening",          "",          "decompose"),   # no ticker
    ("conversation",       "",          "conversation"),  # 1.2 fix — no fabricated report
]


@pytest.mark.parametrize("intent,ticker,expected", GOLDEN_ROUTES)
def test_route_after_clarify_golden(intent, ticker, expected):
    from agents.graph import _route_after_clarify
    state = {"intent": intent, "ticker": ticker}
    assert _route_after_clarify(state) == expected


def test_route_after_clarify_no_intent_decompose():
    from agents.graph import _route_after_clarify
    assert _route_after_clarify({"intent": "", "ticker": ""}) == "decompose"


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — ticker extraction (1.4 uppercase-first + stopword guard)
# ═══════════════════════════════════════════════════════════════════════════════

def test_raw_tickers_uppercase_first():
    # Real (uppercase) tickers are matched as written; no whole-sentence .upper().
    assert raw_tickers("phân tích HPG và VCB") == ["HPG", "VCB"]
    # Case-insensitive fallback fires only when the source has no uppercase token.
    assert raw_tickers("hpg vcb") == ["HPG", "VCB"]
    # Vietnamese lowercase words ("tin") are not uppercased when a real ticker is present.
    assert raw_tickers("tin tức về HPG") == ["HPG"]


def test_raw_tickers_stopwords():
    # Financial-metric tokens that would match [A-Z]{2,5} are filtered out.
    assert raw_tickers("ROE của HPG") == ["HPG"]
    assert raw_tickers("EPS và P/B của VCB") == ["VCB"]


def test_extract_tickers_false_positive_universe(monkeypatch):
    """Lowercase Vietnamese words are not misread as tickers (1.4)."""
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB", "TIN", "NAM", "HAI"])
    assert extract_tickers("phân tích HPG và VCB") == ["HPG", "VCB"]
    # "tin" (lowercase) must NOT become "TIN" when an uppercase ticker is present.
    assert extract_tickers("tin tức về HPG") == ["HPG"]
    # An actual uppercase ticker IS extracted.
    assert extract_tickers("TIN và HPG") == ["TIN", "HPG"]


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — router ticker validation (1.10)
# ═══════════════════════════════════════════════════════════════════════════════

def test_validate_ticker(monkeypatch):
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    from agents.conversation_router import _validate_ticker
    assert _validate_ticker("hpg") == "HPG"
    assert _validate_ticker(" NGANHANG ") == ""
    assert _validate_ticker("") == ""
    assert _validate_ticker("AAPL") == ""            # foreign code
    assert _validate_ticker("VNINDEX") == ""         # market index → handled by intent, not ticker


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — fallback classify passes history (1.9)
# ═══════════════════════════════════════════════════════════════════════════════

def test_fallback_classify_passes_history(monkeypatch):
    from agents.conversation_router import _fallback_classify
    history = [
        {"role": "user", "content": "phân tích HPG"},
        {"role": "assistant", "content": "báo cáo HPG ..."},
    ]
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("technical_analysis", "HPG", "x")) as m:
        route = _fallback_classify("phân tích sâu hơn", history)
    m.assert_called_once()
    assert m.call_args.kwargs.get("messages") == history
    assert route is not None and route["type"] == "agent"
    assert route["intent"] == "technical_analysis"


def test_fallback_classify_social_returns_none(monkeypatch):
    from agents.conversation_router import _fallback_classify
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        assert _fallback_classify("cảm ơn bạn", []) is None


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — llm_route fail-safe + direct_reply redirect (1.8)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_route_llm_error_returns_text(monkeypatch):
    """LLM exception → short text error, NOT market_brief (1.8)."""
    from agents import conversation_router as cr
    client = MagicMock()
    client.generate.side_effect = RuntimeError("boom")
    route = cr.llm_route("HPG giá hôm nay", [], "sys", client=client)
    assert route["type"] == "text"
    assert route["text"]
    assert route.get("intent", "") != "market_brief"


def test_llm_route_no_toolcall_fallback_agent(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    resp = MagicMock(); resp.tool_calls = []; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("technical_analysis", "HPG", "x")):
        route = cr.llm_route("phân tích kỹ thuật HPG", [], "sys", client=client)
    assert route["type"] == "agent"
    assert route["intent"] == "technical_analysis"
    assert route["ticker"] == "HPG"


def test_llm_route_direct_reply_financial_redirect(monkeypatch):
    """direct_reply misused for a financial query → re-classified to agent (1.8)."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock(); tc.name = "direct_reply"; tc.input = {"text": "ok", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("macro_sector", None, "x")):
        route = cr.llm_route("giá thép hôm nay", [], "sys", client=client)
    assert route["type"] == "agent"
    assert route["intent"] == "macro_sector"


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — cache key for ticker-less follow-up (1.3)
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_cache_node_uses_query_for_tickerless_original(monkeypatch):
    """'phân tích sâu hơn' (no subject) must key off the self-contained query."""
    from core.cache import _question_scope
    captured = {}

    def fake_cache_get(ck):
        captured["ticker"] = ck.ticker
        captured["scope"] = ck.scope
        return None, "miss"

    monkeypatch.setattr("core.cache.cache_get", fake_cache_get)
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])

    from agents.graph import check_cache_node
    state = {
        "original_query": "phân tích sâu hơn",
        "query": "phân tích sâu hơn về ngành ngân hàng",
        "tenant_id": "default",
        "intent": "macro_sector",
        "ticker": "",
    }
    check_cache_node(state)
    assert captured["scope"] == _question_scope("phân tích sâu hơn về ngành ngân hàng")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — llm_route golden set (real LLM, opt-in)
# ═══════════════════════════════════════════════════════════════════════════════

_ROUTE_SYSTEM = (
    "Bạn là trợ lý phân tích tài chính chứng khoán Việt Nam. "
    "Trả lời bằng tiếng Việt."
)

# (query, expected_intent, expected_ticker)
GOLDEN_LLM_ROUTE = [
    ("RSI và MACD của HPG đang thế nào?",         "technical_analysis", "HPG"),
    ("P/E của HPG so với trung bình ngành thép?", "valuation",          "HPG"),
    ("so sánh BID và CTG",                         "valuation",          "BID"),
    ("tỷ giá USD/VND hôm nay thế nào?",            "macro_sector",       ""),
    ("tổng quan thị trường chứng khoán hôm nay",   "market_brief",       ""),
    ("HPG có nên mua không?",                      "investment_case",    "HPG"),
    ("Xin chào, bạn tên gì?",                      None,                 ""),  # → type=text
]


@pytest.mark.e2e
@pytest.mark.parametrize("query,expected_intent,expected_ticker", GOLDEN_LLM_ROUTE)
def test_llm_route_golden(query, expected_intent, expected_ticker):
    from agents.conversation_router import llm_route
    route = llm_route(query, [], _ROUTE_SYSTEM)
    if expected_intent is None:
        assert route["type"] == "text", f"expected direct reply, got {route}"
        return
    assert route["type"] == "agent", f"expected agent, got {route}"
    assert route["intent"] == expected_intent, f"got {route['intent']!r}, expected {expected_intent!r}"
    if expected_ticker:
        assert route["ticker"] == expected_ticker, f"got {route['ticker']!r}"
