"""
tests/test_hybrid_router.py — Unit + end-to-end tests for the hybrid router.

Unit tests (fast, no LLM):
  - Keyword router handles clear financial queries — both EN and VI
  - classify_hybrid triggers LLM only on uncertain results (conversation miss
    or ticker-only default) and only for >= 3-word queries
  - LLM result accepted when non-conversation intent in INTENTS
  - LLM result rejected when conversation / invalid / None
  - Ticker merging: keyword ticker preserved when LLM omits it; LLM ticker
    used when explicitly provided
  - All keyword hits across all 10 intents verified — EN and VI
  - New English screening keywords verified
  - llm_router generate() call shape verified
  - llm_router edge cases: missing keys, empty text, text-scan order

End-to-end tests (real LLM, marked @pytest.mark.e2e):
  - English and Vietnamese ambiguous queries route correctly
  - Mixed-language ticker queries route to right intent
  - Pure chat stays as conversation

Run all unit tests:
    pytest tests/test_hybrid_router.py -v -m "not e2e"

Run with e2e (slow, costs tokens):
    pytest tests/test_hybrid_router.py -v -m e2e
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

load_dotenv()

import pytest

from agents.query_router import RouterResult, classify, classify_hybrid


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_result(intent: str, ticker: str | None = None) -> RouterResult:
    return RouterResult(intent=intent, ticker=ticker, reason=f"llm:test")


def _mock_client(intent: str, ticker: str | None = None):
    """Return a mock LLM client whose tool_calls return the given intent."""
    tc = MagicMock()
    tc.input = {"intent": intent, "ticker": ticker, "reason": "test mock"}
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    return client


# ── Unit: keyword router still works independently ───────────────────────────

def test_keyword_clear_technical():
    r = classify("phân tích kỹ thuật HPG")
    assert r.intent == "technical_analysis"
    assert r.ticker == "HPG"


def test_keyword_clear_fundamentals():
    r = classify("P/E của VNM là bao nhiêu?")
    assert r.intent == "fundamentals"
    assert r.ticker == "VNM"


def test_keyword_screening():
    r = classify("lọc cổ phiếu ROE cao nhất")
    assert r.intent == "screening"


def test_keyword_market_brief():
    r = classify("VNINDEX hôm nay thế nào?")
    assert r.intent == "market_brief"


def test_keyword_investment_case():
    r = classify("có nên mua HPG không?")
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


def test_keyword_conversation_greeting():
    r = classify("xin chào")
    assert r.intent == "conversation"


# ── Unit: classify_hybrid — LLM NOT called for known intents ─────────────────

def test_hybrid_no_llm_for_keyword_hit():
    """Keyword hit → LLM must never be called."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("phân tích kỹ thuật FPT")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.ticker == "FPT"


def test_hybrid_no_llm_for_keyword_hit_fundamentals():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("ROE của TCB năm 2024")
        mock_llm.assert_not_called()
    assert r.intent == "fundamentals"


# ── Unit: classify_hybrid — ticker-only default also triggers LLM ─────────────

def test_hybrid_llm_called_on_ticker_default():
    """Ticker found but no intent keyword → technical_analysis(default) → LLM tried."""
    query = "Is HPG worth buying right now?"
    kw_result = classify(query)
    assert kw_result.intent == "technical_analysis", "prereq: ticker-only default"
    assert kw_result.reason.endswith("default"), "prereq: reason must end with default"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", "HPG")) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


def test_hybrid_no_llm_when_technical_keyword_hit():
    """Technical keyword found (not ticker-default) → LLM not called."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("What is the RSI trend for FPT?")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.reason == "technical keyword"


def test_hybrid_ticker_default_keeps_keyword_if_llm_returns_conversation():
    """Ticker-default → LLM returns conversation → keep keyword technical_analysis."""
    query = "HPG news today?"
    kw_result = classify(query)
    # HPG ticker default → technical_analysis OR news keyword may fire
    # Either way, if LLM says conversation we keep keyword result
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("conversation")):
        r = classify_hybrid(query)
    assert r.intent == kw_result.intent


# ── Unit: classify_hybrid — LLM called on 'conversation' miss ────────────────

def test_hybrid_llm_called_on_conversation_miss():
    """English query escaping all keyword sets → keyword returns 'conversation' → LLM called."""
    # Deliberately avoids ALL-CAPS tickers, Vietnamese text, 'sector', 'macro', ' pe', 'roe', etc.
    query = "how does earnings growth affect stock prices over time?"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("fundamentals")) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == "fundamentals"


def test_hybrid_uses_llm_result_when_non_conversation():
    """No ticker, no keyword match → keyword gives conversation → LLM result accepted."""
    query = "can you explain what makes companies grow over time?"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("fundamentals")):
        r = classify_hybrid(query)
    assert r.intent == "fundamentals"
    assert r.reason.startswith("llm:")


def test_hybrid_ignores_llm_conversation_result():
    """LLM also returns 'conversation' → keep keyword result."""
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("conversation")):
        r = classify_hybrid("what do you think about life in general today?")
    assert r.intent == "conversation"
    assert r.reason == "no financial intent detected"


# ── Unit: classify_hybrid — short query skips LLM ────────────────────────────

def test_hybrid_short_query_skips_llm_one_word():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("hello")
        mock_llm.assert_not_called()
    assert r.intent == "conversation"


def test_hybrid_short_query_skips_llm_two_words():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("xin chào")
        mock_llm.assert_not_called()
    assert r.intent == "conversation"


def test_hybrid_three_words_triggers_llm_on_miss():
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("macro_sector")) as mock_llm:
        r = classify_hybrid("steel prices today")
        mock_llm.assert_called_once()
    assert r.intent == "macro_sector"


# ── Unit: classify_hybrid — LLM failure fallback ─────────────────────────────

def test_hybrid_llm_exception_falls_back_to_keyword():
    """LLM error → silently return keyword result."""
    # No ticker, no keywords → keyword returns conversation
    query = "what are the key factors when choosing stocks to buy?"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", side_effect=RuntimeError("timeout")):
        r = classify_hybrid(query)
    assert r.intent == "conversation"  # keyword result preserved


def test_hybrid_llm_returns_none_falls_back():
    with patch("agents.llm_router.llm_classify", return_value=None):
        r = classify_hybrid("Should I buy banking stocks now?")
    assert r.intent == "conversation"


def test_hybrid_llm_invalid_intent_falls_back():
    """LLM returns intent not in INTENTS → classify_hybrid rejects it → keyword result kept."""
    query = "random financial question without keywords here"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("unknown_intent")):
        r = classify_hybrid(query)
    assert r.intent == "conversation"


# ── Unit: llm_router tool-call parsing ───────────────────────────────────────

def test_llm_router_parses_tool_call():
    from agents.llm_router import llm_classify
    client = _mock_client("price_action", "MBB")
    r = llm_classify("MBB money flow today", client=client)
    assert r is not None
    assert r.intent == "price_action"
    assert r.ticker == "MBB"
    assert r.reason.startswith("llm:")


def test_llm_router_normalises_invalid_intent():
    from agents.llm_router import llm_classify
    tc = MagicMock()
    tc.input = {"intent": "INVALID", "reason": "bad"}
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "conversation"


def test_llm_router_text_scan_fallback():
    from agents.llm_router import llm_classify
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = "This should be routed to fundamentals based on the content."
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "fundamentals"


def test_llm_router_returns_none_on_exception():
    from agents.llm_router import llm_classify
    client = MagicMock()
    client.generate.side_effect = ConnectionError("network error")
    r = llm_classify("some query", client=client)
    assert r is None


def test_llm_router_empty_ticker_normalised():
    from agents.llm_router import llm_classify
    client = _mock_client("macro_sector", "   ")
    r = llm_classify("oil prices impact", client=client)
    assert r is not None
    assert r.ticker is None  # whitespace-only ticker stripped


# ── Unit: Vietnamese ticker-default → LLM triggered (mocked) ────────────────

@pytest.mark.parametrize("query,llm_intent,expected_ticker", [
    # Queries with ticker but NO matching Vietnamese keyword phrase
    ("HPG có triển vọng gì trong năm nay?",  "investment_case",  "HPG"),
    ("VCB đang giao dịch ở vùng giá nào?",   "price_action",     "VCB"),
    ("FPT cần theo dõi những gì gần đây?",   "news_sentiment",   "FPT"),
    ("TCB liệu có phục hồi trong quý tới?",  "technical_analysis", "TCB"),
    ("MBB sẽ đi về đâu trong thời gian tới?",  "technical_analysis", "MBB"),
])
def test_viet_ticker_default_triggers_llm(query, llm_intent, expected_ticker):
    """Vietnamese query: ticker found but no intent keyword → ticker-default → LLM called."""
    kw = classify(query)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default"), \
        f"prereq failed for {query!r}: intent={kw.intent!r} reason={kw.reason!r}"
    with patch("agents.llm_router.llm_classify",
               return_value=_make_llm_result(llm_intent, expected_ticker)) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == llm_intent
    assert r.ticker == expected_ticker


# ── Unit: Vietnamese conversation miss → LLM triggered (mocked) ──────────────

@pytest.mark.parametrize("query,llm_intent", [
    ("Tôi muốn học cách phân tích cổ phiếu từ đầu", "fundamentals"),
    ("Đầu tư vào thị trường cổ phiếu cần lưu ý gì?",  "investment_case"),
    ("Nên bắt đầu tìm hiểu về chứng khoán như thế nào?", "conversation"),
    ("Chiến lược nắm giữ dài hạn hiệu quả ra sao?",    "investment_case"),
])
def test_viet_conversation_miss_triggers_llm(query, llm_intent):
    """Vietnamese query with no keyword match → conversation → LLM called."""
    kw = classify(query)
    assert kw.intent == "conversation", \
        f"prereq failed for {query!r}: got {kw.intent!r} (reason={kw.reason!r})"
    with patch("agents.llm_router.llm_classify",
               return_value=_make_llm_result(llm_intent)) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    if llm_intent == "conversation":
        assert r.intent == "conversation"
        assert r.reason == "no financial intent detected"  # keyword result kept
    else:
        assert r.intent == llm_intent


# ── Unit: Vietnamese clear keyword hits — LLM must not be called ─────────────

@pytest.mark.parametrize("query,expected_intent", [
    ("giá hiện tại của HPG là bao nhiêu?",          "price_action"),
    ("active buy VNM hôm nay ra sao?",               "price_action"),
    ("phân tích kỹ thuật FPT tuần này",              "technical_analysis"),
    ("support resistance của VCB ở đâu?",            "technical_analysis"),
    ("P/E của HPG so với ngành thép",                "fundamentals"),
    ("kết quả tài chính quý 3 của VNM",              "fundamentals"),
    ("tỷ giá USD VND ảnh hưởng thế nào?",           "macro_sector"),
    ("giá thép HRC tuần này biến động ra sao?",     "macro_sector"),
    ("tin tức mới nhất về HPG",                     "news_sentiment"),
    ("tâm lý nhà đầu tư đang thế nào?",             "news_sentiment"),
    ("thị trường chứng khoán tuần này",             "market_brief"),
    ("nên mua hay bán VCB hiện tại?",               "investment_case"),
    ("đánh giá tổng thể về HPG",                    "investment_case"),
    ("lọc cổ phiếu có ROE cao nhất năm 2024",       "screening"),
    ("mã nào đang tăng trưởng tốt nhất?",           "screening"),
])
def test_viet_keyword_hits_no_llm(query, expected_intent):
    """Clear Vietnamese keyword matches must never trigger LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_not_called()
    assert r.intent == expected_intent, f"query={query!r} → got {r.intent}"


# ── Unit: Vietnamese ticker preservation ─────────────────────────────────────

def test_viet_ticker_preserved_when_llm_omits_it():
    """Vietnamese ticker-default: LLM returns None ticker → keyword ticker preserved."""
    query = "HPG có triển vọng gì trong năm nay?"
    kw = classify(query)
    assert kw.ticker == "HPG"
    with patch("agents.llm_router.llm_classify",
               return_value=_make_llm_result("investment_case", None)):
        r = classify_hybrid(query)
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


# ── Unit: all strong keyword hits never trigger LLM ─────────────────────────

@pytest.mark.parametrize("query,expected_intent", [
    ("dòng tiền HPG hôm nay",          "price_action"),
    ("khối ngoại mua ròng VNM",        "price_action"),
    ("MACD FPT tuần này",              "technical_analysis"),
    ("xu hướng breakout VCB",          "technical_analysis"),
    ("tin tức về HPG",                 "news_sentiment"),
    ("sentiment cổ phiếu HPG hôm nay",  "news_sentiment"),
    ("tỷ giá USD VND tác động",        "macro_sector"),
    ("giá dầu brent tuần này",         "macro_sector"),
    ("thị trường chứng khoán hôm nay", "market_brief"),
    ("VNINDEX đóng cửa phiên này",     "market_brief"),
    ("khuyến nghị mua bán HPG",        "investment_case"),
    ("có nên đầu tư vào VNM không",   "investment_case"),
    ("lọc cổ phiếu ROE cao nhất",      "screening"),
    ("top 5 mã tăng trưởng tốt nhất",  "screening"),
])
def test_keyword_intents_never_trigger_llm(query, expected_intent):
    """All clear keyword hits must not call LLM — verified per intent."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_not_called()
    assert r.intent == expected_intent, f"query={query!r} → got {r.intent}"


# ── Unit: new English screening keywords ─────────────────────────────────────

@pytest.mark.parametrize("query", [
    "which stocks have the highest ROE in 2024?",
    "what stocks have the best revenue growth?",
    "find stocks with strong earnings",
    "top stocks by profit margin this year",
    "best stocks to consider in banking",
    "highest roe companies by revenue growth",
    "rank stocks by return on equity",
    "list stocks with growing revenue",
])
def test_new_screening_keywords_route_to_screening(query):
    """New English screening patterns must route directly via keyword (no LLM)."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_not_called()
    assert r.intent == "screening", f"query={query!r} → got {r.intent}"


# ── Unit: ticker-default boundary — word count threshold ─────────────────────

def test_ticker_default_two_words_skips_llm():
    """Ticker only, 2 words → below threshold → no LLM."""
    query = "phân tích HPG"  # 3 tokens but let's try 2-word English
    # Build a definitely 2-word query with ticker default
    # "Analyze HPG" → keyword: ticker HPG default (2 words)
    query2 = "analyze HPG"
    kw = classify(query2)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default")
    assert len(query2.split()) == 2
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query2)
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"


def test_ticker_default_three_words_triggers_llm():
    """Ticker + 2 other words → meets threshold → LLM triggered."""
    query = "buy or sell HPG"
    kw = classify(query)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default")
    assert len(query.split()) >= 3
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", "HPG")) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == "investment_case"


def test_ticker_plus_technical_keyword_not_default():
    """Ticker + technical keyword → reason is 'technical keyword', not default → no LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("RSI of HPG this week")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.reason == "technical keyword"


def test_ticker_plus_investment_keyword_not_default():
    """Ticker + investment keyword → keyword wins → no LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("có nên mua HPG không?")
        mock_llm.assert_not_called()
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


# ── Unit: ticker preservation when LLM doesn't extract ticker ────────────────

def test_hybrid_preserves_keyword_ticker_when_llm_omits_it():
    """Ticker found by keyword regex but LLM returns ticker=None → use keyword's ticker."""
    query = "Is HPG worth buying for long term?"
    kw = classify(query)
    assert kw.ticker == "HPG", "prereq: keyword router must extract HPG"
    # LLM returns correct intent but misses ticker
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", None)):
        r = classify_hybrid(query)
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"   # preserved from keyword result


def test_hybrid_uses_llm_ticker_when_provided():
    """LLM returns a non-None ticker → use LLM's ticker over keyword's ticker."""
    # Must be ticker-only default (no keyword match) so LLM is called.
    # "Is HPG or TCB better?" → no keyword hit → technical_analysis(ticker=HPG, default)
    query = "Is HPG or TCB the better choice?"
    kw = classify(query)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default"), \
        f"prereq failed: got intent={kw.intent!r} reason={kw.reason!r}"
    assert kw.ticker == "HPG", "prereq: keyword picks HPG first"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", "TCB")):
        r = classify_hybrid(query)
    assert r.intent == "investment_case"
    assert r.ticker == "TCB"   # LLM's ticker used when explicitly provided


def test_hybrid_no_ticker_when_neither_finds_one():
    """No ticker in query, LLM also returns None → ticker is None."""
    query = "what happens to steel stocks during inflation?"
    assert classify(query).intent == "conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("macro_sector", None)):
        r = classify_hybrid(query)
    assert r.intent == "macro_sector"
    assert r.ticker is None


# ── Unit: llm_router — generate() call shape verified ────────────────────────

def test_llm_router_generate_called_with_correct_params():
    """Verify generate() receives correct tools, messages, and max_tokens."""
    from agents.llm_router import llm_classify, _TOOL
    from llm.types import Message

    resp = MagicMock()
    resp.tool_calls = []
    resp.text = "conversation"
    client = MagicMock()
    client.generate.return_value = resp

    llm_classify("test query here", client=client)

    client.generate.assert_called_once()
    kwargs = client.generate.call_args.kwargs
    assert kwargs["tools"] == [_TOOL]
    assert kwargs["max_tokens"] == 256
    msgs = kwargs["messages"]
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "test query here"


def test_llm_router_system_prompt_contains_all_intents():
    """System prompt must mention all 10 intent labels."""
    from agents.llm_router import _SYSTEM, INTENTS
    for intent in INTENTS:
        assert intent in _SYSTEM, f"intent '{intent}' missing from system prompt"


# ── Unit: llm_router — tool_calls edge cases ─────────────────────────────────

def test_llm_router_tool_call_missing_intent_key():
    """tool_calls present but 'intent' key absent → defaults to conversation."""
    from agents.llm_router import llm_classify
    tc = MagicMock()
    tc.input = {"reason": "no intent key here"}   # no "intent"
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "conversation"


def test_llm_router_tool_call_none_ticker_preserved():
    """Tool call with ticker explicitly absent → r.ticker is None."""
    from agents.llm_router import llm_classify
    tc = MagicMock()
    tc.input = {"intent": "market_brief", "reason": "market question"}
    # no "ticker" key
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("vnindex today", client=client)
    assert r is not None
    assert r.intent == "market_brief"
    assert r.ticker is None


def test_llm_router_text_scan_first_match_wins():
    """Multiple intent words in text → first in INTENTS tuple order wins."""
    from agents.llm_router import llm_classify, INTENTS
    # Put two intents in the text; first in INTENTS order should win
    first_intent = INTENTS[0]   # "price_action"
    second_intent = INTENTS[1]  # "technical_analysis"
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = f"this is {second_intent} but also {first_intent} content"
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("query", client=client)
    assert r is not None
    assert r.intent == first_intent


def test_llm_router_empty_text_returns_none_when_no_toolcall():
    """No tool_calls and empty text → no intent found → returns None."""
    from agents.llm_router import llm_classify
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is None


# ── Unit: reason field integrity ─────────────────────────────────────────────

def test_keyword_result_reason_never_starts_with_llm():
    """When keyword router handles query, reason must not start with 'llm:'."""
    queries = [
        "dòng tiền HPG hôm nay",
        "RSI FPT",
        "xin chào",
        "VNINDEX hôm nay",
    ]
    for q in queries:
        r = classify_hybrid(q)
        assert not r.reason.startswith("llm:"), f"query={q!r} got llm: reason={r.reason!r}"


def test_llm_result_reason_starts_with_llm():
    """When LLM handles query, returned reason must start with 'llm:'."""
    query = "how does earnings growth work over time?"
    assert classify(query).intent == "conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("fundamentals")):
        r = classify_hybrid(query)
    assert r.reason.startswith("llm:")


# ── E2E: real LLM calls (slow, opt-in) ───────────────────────────────────────

@pytest.mark.e2e
def test_e2e_english_investment_query():
    """Ticker + English buy intent → keyword returns ticker-default → LLM upgrades to investment_case."""
    r = classify_hybrid("Is HPG worth buying right now?")
    assert r.intent == "investment_case", f"got: {r.intent} / reason: {r.reason}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_mixed_language_fundamentals():
    """Ticker + English fundamentals → keyword returns ticker-default → LLM upgrades."""
    r = classify_hybrid("Tell me about VNM revenue and profit growth")
    assert r.intent in ("fundamentals", "qa_document"), f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_english_technical():
    """Technical question in English."""
    r = classify_hybrid("What is the RSI trend for FPT stock?")
    assert r.intent == "technical_analysis", f"got: {r.intent}"
    assert r.ticker == "FPT"


@pytest.mark.e2e
def test_e2e_english_screening():
    """Screening intent via English."""
    r = classify_hybrid("Which stocks have the highest ROE in 2024?")
    assert r.intent == "screening", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_market_question_english():
    """Market overview question in English."""
    r = classify_hybrid("How is the Vietnamese stock market doing today?")
    assert r.intent == "market_brief", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_pure_chat_stays_conversation():
    """Pure chat should not be hijacked by LLM into a financial intent."""
    r = classify_hybrid("What is the weather like in Hanoi today?")
    assert r.intent == "conversation", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_mixed_viet_english_investment():
    """Ticker + mixed-language intent → keyword returns ticker-default → LLM upgrades."""
    r = classify_hybrid("HPG có worth mua vào lúc này không?")
    assert r.intent == "investment_case", f"got: {r.intent}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_macro_no_ticker():
    """Macro question with no ticker."""
    r = classify_hybrid("How does USD/VND rate affect steel stocks?")
    assert r.intent in ("macro_sector", "fundamentals"), f"got: {r.intent}"


# ── E2E: Vietnamese queries ───────────────────────────────────────────────────

@pytest.mark.e2e
def test_e2e_viet_ticker_default_investment():
    """Vietnamese: ticker + implicit buy intent → keyword ticker-default → LLM upgrades."""
    r = classify_hybrid("HPG có nên giữ lâu dài không?")
    assert r.intent == "investment_case", f"got: {r.intent}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_viet_ticker_default_fundamentals():
    """Vietnamese: ticker + 'financial situation' phrasing → keyword ticker-default → LLM upgrades."""
    r = classify_hybrid("Cho tôi biết tình hình tài chính của TCB")
    assert r.intent in ("fundamentals", "investment_case", "qa_document"), f"got: {r.intent}"
    assert r.ticker == "TCB"


@pytest.mark.e2e
def test_e2e_viet_screening():
    """Vietnamese: 'cổ phiếu nào' → keyword screening hit, LLM not needed."""
    r = classify_hybrid("Cổ phiếu nào đang tốt hiện nay?")
    assert r.intent == "screening", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_pure_chat():
    """Vietnamese: off-topic question stays conversation."""
    r = classify_hybrid("Hà Nội hôm nay thời tiết thế nào?")
    assert r.intent == "conversation", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_investment_no_ticker():
    """Vietnamese: investment question with no ticker → LLM classifies without ticker."""
    r = classify_hybrid("Đầu tư vào chứng khoán lúc này có hợp lý không?")
    assert r.intent in ("investment_case", "conversation"), f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_market_brief():
    """Vietnamese: market overview phrasing → keyword hit, no LLM."""
    r = classify_hybrid("Thị trường chứng khoán hôm nay diễn biến thế nào?")
    assert r.intent == "market_brief", f"got: {r.intent}"
