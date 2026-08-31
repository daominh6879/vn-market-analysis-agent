"""
tests/test_hybrid_router.py â€” Unit + end-to-end tests for the hybrid router.

Unit tests (fast, no LLM):
  - Keyword router handles clear financial queries â€” both EN and VI
  - classify_hybrid triggers LLM only on uncertain results (conversation miss
    or ticker-only default) and only for >= 3-word queries
  - LLM result accepted when non-conversation intent in INTENTS
  - LLM result rejected when conversation / invalid / None
  - Ticker merging: keyword ticker preserved when LLM omits it; LLM ticker
    used when explicitly provided
  - All keyword hits across all 10 intents verified â€” EN and VI
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

from agents.classifier import RouterResult, classify, classify_hybrid


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ Unit: keyword router still works independently â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_keyword_clear_technical():
    r = classify("phÃ¢n tÃ­ch ká»¹ thuáº­t HPG")
    assert r.intent == "technical_analysis"
    assert r.ticker == "HPG"


def test_keyword_clear_fundamentals():
    r = classify("P/E cá»§a VNM lÃ  bao nhiÃªu?")
    assert r.intent == "fundamentals"
    assert r.ticker == "VNM"


def test_keyword_screening():
    r = classify("lá»c cá»• phiáº¿u ROE cao nháº¥t")
    assert r.intent == "screening"


def test_keyword_market_brief():
    r = classify("VNINDEX hÃ´m nay tháº¿ nÃ o?")
    assert r.intent == "market_brief"


def test_keyword_investment_case():
    r = classify("cÃ³ nÃªn mua HPG khÃ´ng?")
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


def test_keyword_conversation_greeting():
    r = classify("xin chÃ o")
    assert r.intent == "conversation"


# â”€â”€ Unit: classify_hybrid â€” LLM NOT called for known intents â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_no_llm_for_keyword_hit():
    """Keyword hit â†’ LLM must never be called."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("phÃ¢n tÃ­ch ká»¹ thuáº­t FPT")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.ticker == "FPT"


def test_hybrid_no_llm_for_keyword_hit_fundamentals():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("ROE cá»§a TCB nÄƒm 2024")
        mock_llm.assert_not_called()
    assert r.intent == "fundamentals"


# â”€â”€ Unit: classify_hybrid â€” ticker-only default also triggers LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_llm_called_on_ticker_default():
    """Ticker found but no intent keyword â†’ technical_analysis(default) â†’ LLM tried."""
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
    """Technical keyword found (not ticker-default) â†’ LLM not called."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("What is the RSI trend for FPT?")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.reason == "technical keyword"


def test_hybrid_ticker_default_keeps_keyword_if_llm_returns_conversation():
    """Ticker-default â†’ LLM returns conversation â†’ keep keyword technical_analysis."""
    query = "HPG news today?"
    kw_result = classify(query)
    # HPG ticker default â†’ technical_analysis OR news keyword may fire
    # Either way, if LLM says conversation we keep keyword result
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("conversation")):
        r = classify_hybrid(query)
    assert r.intent == kw_result.intent


# â”€â”€ Unit: classify_hybrid â€” LLM called on 'conversation' miss â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_llm_called_on_conversation_miss():
    """English query escaping all keyword sets â†’ keyword returns 'conversation' â†’ LLM called."""
    # Deliberately avoids ALL-CAPS tickers, Vietnamese text, 'sector', 'macro', ' pe', 'roe', etc.
    query = "how does earnings growth affect stock prices over time?"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("fundamentals")) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == "fundamentals"


def test_hybrid_uses_llm_result_when_non_conversation():
    """No ticker, no keyword match â†’ keyword gives conversation â†’ LLM result accepted."""
    query = "can you explain what makes companies grow over time?"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("fundamentals")):
        r = classify_hybrid(query)
    assert r.intent == "fundamentals"
    assert r.reason.startswith("llm:")


def test_hybrid_ignores_llm_conversation_result():
    """LLM also returns 'conversation' â†’ keep keyword result."""
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("conversation")):
        r = classify_hybrid("what do you think about life in general today?")
    assert r.intent == "conversation"
    assert r.reason == "no financial intent detected"


# â”€â”€ Unit: classify_hybrid â€” short query skips LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_short_query_skips_llm_one_word():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("hello")
        mock_llm.assert_not_called()
    assert r.intent == "conversation"


def test_hybrid_short_query_skips_llm_two_words():
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("xin chÃ o")
        mock_llm.assert_not_called()
    assert r.intent == "conversation"


def test_hybrid_three_words_triggers_llm_on_miss():
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("macro_sector")) as mock_llm:
        r = classify_hybrid("steel prices today")
        mock_llm.assert_called_once()
    assert r.intent == "macro_sector"


# â”€â”€ Unit: classify_hybrid â€” LLM failure fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_llm_exception_falls_back_to_keyword():
    """LLM error â†’ silently return keyword result."""
    # No ticker, no keywords â†’ keyword returns conversation
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
    """LLM returns intent not in INTENTS â†’ classify_hybrid rejects it â†’ keyword result kept."""
    query = "random financial question without keywords here"
    assert classify(query).intent == "conversation", "prereq: keyword router must return conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("unknown_intent")):
        r = classify_hybrid(query)
    assert r.intent == "conversation"


# â”€â”€ Unit: llm_router tool-call parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_llm_router_parses_tool_call():
    from agents.classifier import llm_classify
    client = _mock_client("price_action", "MBB")
    r = llm_classify("MBB money flow today", client=client)
    assert r is not None
    assert r.intent == "price_action"
    assert r.ticker == "MBB"
    assert r.reason.startswith("llm:")


def test_llm_router_normalises_invalid_intent():
    from agents.classifier import llm_classify
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
    from agents.classifier import llm_classify
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = "This should be routed to fundamentals based on the content."
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "fundamentals"


def test_llm_router_returns_none_on_exception():
    from agents.classifier import llm_classify
    client = MagicMock()
    client.generate.side_effect = ConnectionError("network error")
    r = llm_classify("some query", client=client)
    assert r is None


def test_llm_router_empty_ticker_normalised():
    from agents.classifier import llm_classify
    client = _mock_client("macro_sector", "   ")
    r = llm_classify("oil prices impact", client=client)
    assert r is not None
    assert r.ticker is None  # whitespace-only ticker stripped


# â”€â”€ Unit: Vietnamese ticker-default â†’ LLM triggered (mocked) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("query,llm_intent,expected_ticker", [
    # Queries with ticker but NO matching Vietnamese keyword phrase
    ("HPG cÃ³ triá»ƒn vá»ng gÃ¬ trong nÄƒm nay?",  "investment_case",  "HPG"),
    ("VCB Ä‘ang giao dá»‹ch á»Ÿ vÃ¹ng giÃ¡ nÃ o?",   "price_action",     "VCB"),
    ("FPT cáº§n theo dÃµi nhá»¯ng gÃ¬ gáº§n Ä‘Ã¢y?",   "news_sentiment",   "FPT"),
    ("TCB liá»‡u cÃ³ phá»¥c há»“i trong quÃ½ tá»›i?",  "technical_analysis", "TCB"),
    ("MBB sáº½ Ä‘i vá» Ä‘Ã¢u trong thá»i gian tá»›i?",  "technical_analysis", "MBB"),
])
def test_viet_ticker_default_triggers_llm(query, llm_intent, expected_ticker):
    """Vietnamese query: ticker found but no intent keyword â†’ ticker-default â†’ LLM called."""
    kw = classify(query)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default"), \
        f"prereq failed for {query!r}: intent={kw.intent!r} reason={kw.reason!r}"
    with patch("agents.llm_router.llm_classify",
               return_value=_make_llm_result(llm_intent, expected_ticker)) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == llm_intent
    assert r.ticker == expected_ticker


# â”€â”€ Unit: Vietnamese conversation miss â†’ LLM triggered (mocked) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("query,llm_intent", [
    ("TÃ´i muá»‘n há»c cÃ¡ch phÃ¢n tÃ­ch cá»• phiáº¿u tá»« Ä‘áº§u", "fundamentals"),
    ("Äáº§u tÆ° vÃ o thá»‹ trÆ°á»ng cá»• phiáº¿u cáº§n lÆ°u Ã½ gÃ¬?",  "investment_case"),
    ("NÃªn báº¯t Ä‘áº§u tÃ¬m hiá»ƒu vá» chá»©ng khoÃ¡n nhÆ° tháº¿ nÃ o?", "conversation"),
    ("Chiáº¿n lÆ°á»£c náº¯m giá»¯ dÃ i háº¡n hiá»‡u quáº£ ra sao?",    "investment_case"),
])
def test_viet_conversation_miss_triggers_llm(query, llm_intent):
    """Vietnamese query with no keyword match â†’ conversation â†’ LLM called."""
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


# â”€â”€ Unit: Vietnamese clear keyword hits â€” LLM must not be called â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("query,expected_intent", [
    ("giÃ¡ hiá»‡n táº¡i cá»§a HPG lÃ  bao nhiÃªu?",          "price_action"),
    ("active buy VNM hÃ´m nay ra sao?",               "price_action"),
    ("phÃ¢n tÃ­ch ká»¹ thuáº­t FPT tuáº§n nÃ y",              "technical_analysis"),
    ("support resistance cá»§a VCB á»Ÿ Ä‘Ã¢u?",            "technical_analysis"),
    ("P/E cá»§a HPG so vá»›i ngÃ nh thÃ©p",                "fundamentals"),
    ("káº¿t quáº£ tÃ i chÃ­nh quÃ½ 3 cá»§a VNM",              "fundamentals"),
    ("tá»· giÃ¡ USD VND áº£nh hÆ°á»Ÿng tháº¿ nÃ o?",           "macro_sector"),
    ("giÃ¡ thÃ©p HRC tuáº§n nÃ y biáº¿n Ä‘á»™ng ra sao?",     "macro_sector"),
    ("tin tá»©c má»›i nháº¥t vá» HPG",                     "news_sentiment"),
    ("tÃ¢m lÃ½ nhÃ  Ä‘áº§u tÆ° Ä‘ang tháº¿ nÃ o?",             "news_sentiment"),
    ("thá»‹ trÆ°á»ng chá»©ng khoÃ¡n tuáº§n nÃ y",             "market_brief"),
    ("nÃªn mua hay bÃ¡n VCB hiá»‡n táº¡i?",               "investment_case"),
    ("Ä‘Ã¡nh giÃ¡ tá»•ng thá»ƒ vá» HPG",                    "investment_case"),
    ("lá»c cá»• phiáº¿u cÃ³ ROE cao nháº¥t nÄƒm 2024",       "screening"),
    ("mÃ£ nÃ o Ä‘ang tÄƒng trÆ°á»Ÿng tá»‘t nháº¥t?",           "screening"),
])
def test_viet_keyword_hits_no_llm(query, expected_intent):
    """Clear Vietnamese keyword matches must never trigger LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_not_called()
    assert r.intent == expected_intent, f"query={query!r} â†’ got {r.intent}"


# â”€â”€ Unit: Vietnamese ticker preservation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_viet_ticker_preserved_when_llm_omits_it():
    """Vietnamese ticker-default: LLM returns None ticker â†’ keyword ticker preserved."""
    query = "HPG cÃ³ triá»ƒn vá»ng gÃ¬ trong nÄƒm nay?"
    kw = classify(query)
    assert kw.ticker == "HPG"
    with patch("agents.llm_router.llm_classify",
               return_value=_make_llm_result("investment_case", None)):
        r = classify_hybrid(query)
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


# â”€â”€ Unit: all strong keyword hits never trigger LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("query,expected_intent", [
    ("dÃ²ng tiá»n HPG hÃ´m nay",          "price_action"),
    ("khá»‘i ngoáº¡i mua rÃ²ng VNM",        "price_action"),
    ("MACD FPT tuáº§n nÃ y",              "technical_analysis"),
    ("xu hÆ°á»›ng breakout VCB",          "technical_analysis"),
    ("tin tá»©c vá» HPG",                 "news_sentiment"),
    ("sentiment cá»• phiáº¿u HPG hÃ´m nay",  "news_sentiment"),
    ("tá»· giÃ¡ USD VND tÃ¡c Ä‘á»™ng",        "macro_sector"),
    ("giÃ¡ dáº§u brent tuáº§n nÃ y",         "macro_sector"),
    ("thá»‹ trÆ°á»ng chá»©ng khoÃ¡n hÃ´m nay", "market_brief"),
    ("VNINDEX Ä‘Ã³ng cá»­a phiÃªn nÃ y",     "market_brief"),
    ("khuyáº¿n nghá»‹ mua bÃ¡n HPG",        "investment_case"),
    ("cÃ³ nÃªn Ä‘áº§u tÆ° vÃ o VNM khÃ´ng",   "investment_case"),
    ("lá»c cá»• phiáº¿u ROE cao nháº¥t",      "screening"),
    ("top 5 mÃ£ tÄƒng trÆ°á»Ÿng tá»‘t nháº¥t",  "screening"),
])
def test_keyword_intents_never_trigger_llm(query, expected_intent):
    """All clear keyword hits must not call LLM â€” verified per intent."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_not_called()
    assert r.intent == expected_intent, f"query={query!r} â†’ got {r.intent}"


# â”€â”€ Unit: new English screening keywords â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    assert r.intent == "screening", f"query={query!r} â†’ got {r.intent}"


# â”€â”€ Unit: ticker-default boundary â€” word count threshold â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_ticker_default_two_words_skips_llm():
    """Ticker only, 2 words â†’ below threshold â†’ no LLM."""
    query = "phÃ¢n tÃ­ch HPG"  # 3 tokens but let's try 2-word English
    # Build a definitely 2-word query with ticker default
    # "Analyze HPG" â†’ keyword: ticker HPG default (2 words)
    query2 = "analyze HPG"
    kw = classify(query2)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default")
    assert len(query2.split()) == 2
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid(query2)
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"


def test_ticker_default_three_words_triggers_llm():
    """Ticker + 2 other words â†’ meets threshold â†’ LLM triggered."""
    query = "buy or sell HPG"
    kw = classify(query)
    assert kw.intent == "technical_analysis" and kw.reason.endswith("default")
    assert len(query.split()) >= 3
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", "HPG")) as mock_llm:
        r = classify_hybrid(query)
        mock_llm.assert_called_once()
    assert r.intent == "investment_case"


def test_ticker_plus_technical_keyword_not_default():
    """Ticker + technical keyword â†’ reason is 'technical keyword', not default â†’ no LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("RSI of HPG this week")
        mock_llm.assert_not_called()
    assert r.intent == "technical_analysis"
    assert r.reason == "technical keyword"


def test_ticker_plus_investment_keyword_not_default():
    """Ticker + investment keyword â†’ keyword wins â†’ no LLM."""
    with patch("agents.llm_router.llm_classify") as mock_llm:
        r = classify_hybrid("cÃ³ nÃªn mua HPG khÃ´ng?")
        mock_llm.assert_not_called()
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


# â”€â”€ Unit: ticker preservation when LLM doesn't extract ticker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_hybrid_preserves_keyword_ticker_when_llm_omits_it():
    """Ticker found by keyword regex but LLM returns ticker=None â†’ use keyword's ticker."""
    query = "Is HPG worth buying for long term?"
    kw = classify(query)
    assert kw.ticker == "HPG", "prereq: keyword router must extract HPG"
    # LLM returns correct intent but misses ticker
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("investment_case", None)):
        r = classify_hybrid(query)
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"   # preserved from keyword result


def test_hybrid_uses_llm_ticker_when_provided():
    """LLM returns a non-None ticker â†’ use LLM's ticker over keyword's ticker."""
    # Must be ticker-only default (no keyword match) so LLM is called.
    # "Is HPG or TCB better?" â†’ no keyword hit â†’ technical_analysis(ticker=HPG, default)
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
    """No ticker in query, LLM also returns None â†’ ticker is None."""
    query = "what happens to steel stocks during inflation?"
    assert classify(query).intent == "conversation"
    with patch("agents.llm_router.llm_classify", return_value=_make_llm_result("macro_sector", None)):
        r = classify_hybrid(query)
    assert r.intent == "macro_sector"
    assert r.ticker is None


# â”€â”€ Unit: llm_router â€” generate() call shape verified â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_llm_router_generate_called_with_correct_params():
    """Verify generate() receives correct tools, messages, and max_tokens."""
    from agents.classifier import llm_classify, _TOOL
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
    from agents.classifier import _SYSTEM, INTENTS
    for intent in INTENTS:
        assert intent in _SYSTEM, f"intent '{intent}' missing from system prompt"


# â”€â”€ Unit: llm_router â€” tool_calls edge cases â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_llm_router_tool_call_missing_intent_key():
    """tool_calls present but 'intent' key absent â†’ defaults to conversation."""
    from agents.classifier import llm_classify
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
    """Tool call with ticker explicitly absent â†’ r.ticker is None."""
    from agents.classifier import llm_classify
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
    """Multiple intent words in text â†’ first in INTENTS tuple order wins."""
    from agents.classifier import llm_classify, INTENTS
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
    """No tool_calls and empty text â†’ no intent found â†’ returns None."""
    from agents.classifier import llm_classify
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is None


# â”€â”€ Unit: reason field integrity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_keyword_result_reason_never_starts_with_llm():
    """When keyword router handles query, reason must not start with 'llm:'."""
    queries = [
        "dÃ²ng tiá»n HPG hÃ´m nay",
        "RSI FPT",
        "xin chÃ o",
        "VNINDEX hÃ´m nay",
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


# â”€â”€ E2E: real LLM calls (slow, opt-in) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.e2e
def test_e2e_english_investment_query():
    """Ticker + English buy intent â†’ keyword returns ticker-default â†’ LLM upgrades to investment_case."""
    r = classify_hybrid("Is HPG worth buying right now?")
    assert r.intent == "investment_case", f"got: {r.intent} / reason: {r.reason}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_mixed_language_fundamentals():
    """Ticker + English fundamentals â†’ keyword returns ticker-default â†’ LLM upgrades."""
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
    """Ticker + mixed-language intent â†’ keyword returns ticker-default â†’ LLM upgrades."""
    r = classify_hybrid("HPG cÃ³ worth mua vÃ o lÃºc nÃ y khÃ´ng?")
    assert r.intent == "investment_case", f"got: {r.intent}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_macro_no_ticker():
    """Macro question with no ticker."""
    r = classify_hybrid("How does USD/VND rate affect steel stocks?")
    assert r.intent in ("macro_sector", "fundamentals"), f"got: {r.intent}"


# â”€â”€ E2E: Vietnamese queries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.e2e
def test_e2e_viet_ticker_default_investment():
    """Vietnamese: ticker + implicit buy intent â†’ keyword ticker-default â†’ LLM upgrades."""
    r = classify_hybrid("HPG cÃ³ nÃªn giá»¯ lÃ¢u dÃ i khÃ´ng?")
    assert r.intent == "investment_case", f"got: {r.intent}"
    assert r.ticker == "HPG"


@pytest.mark.e2e
def test_e2e_viet_ticker_default_fundamentals():
    """Vietnamese: ticker + 'financial situation' phrasing â†’ keyword ticker-default â†’ LLM upgrades."""
    r = classify_hybrid("Cho tÃ´i biáº¿t tÃ¬nh hÃ¬nh tÃ i chÃ­nh cá»§a TCB")
    assert r.intent in ("fundamentals", "investment_case", "qa_document"), f"got: {r.intent}"
    assert r.ticker == "TCB"


@pytest.mark.e2e
def test_e2e_viet_screening():
    """Vietnamese: 'cá»• phiáº¿u nÃ o' â†’ keyword screening hit, LLM not needed."""
    r = classify_hybrid("Cá»• phiáº¿u nÃ o Ä‘ang tá»‘t hiá»‡n nay?")
    assert r.intent == "screening", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_pure_chat():
    """Vietnamese: off-topic question stays conversation."""
    r = classify_hybrid("HÃ  Ná»™i hÃ´m nay thá»i tiáº¿t tháº¿ nÃ o?")
    assert r.intent == "conversation", f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_investment_no_ticker():
    """Vietnamese: investment question with no ticker â†’ LLM classifies without ticker."""
    r = classify_hybrid("Äáº§u tÆ° vÃ o chá»©ng khoÃ¡n lÃºc nÃ y cÃ³ há»£p lÃ½ khÃ´ng?")
    assert r.intent in ("investment_case", "conversation"), f"got: {r.intent}"


@pytest.mark.e2e
def test_e2e_viet_market_brief():
    """Vietnamese: market overview phrasing â†’ keyword hit, no LLM."""
    r = classify_hybrid("Thá»‹ trÆ°á»ng chá»©ng khoÃ¡n hÃ´m nay diá»…n biáº¿n tháº¿ nÃ o?")
    assert r.intent == "market_brief", f"got: {r.intent}"
