"""
tests/test_hybrid_router.py — Unit tests for agents/classifier.py (llm_classify + classify_hybrid).

The keyword router was removed in the routing refactor (docs/plan-routing-enhancement.md item 1.1);
`classify_hybrid` is now LLM-only (llm_classify + conversation fallback). This file covers:

  - llm_classify tool-call parsing: intent/ticker/reason, invalid intent → conversation,
    whitespace ticker → None, missing intent key, empty text, text-scan fallback order
  - llm_classify failure modes: exception → None, no tool call + no text → None
  - classify_hybrid: LLM result passthrough, conversation fallback on None,
    messages forwarding, out_of_scope passthrough
  - generate() call shape (tools, max_tokens, messages) and _SYSTEM covers all intents

Run:
    pytest tests/test_hybrid_router.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dotenv import load_dotenv

load_dotenv()

from agents.classifier import RouterResult, classify_hybrid, llm_classify


def _make_result(intent: str, ticker: str | None = None) -> RouterResult:
    return RouterResult(intent=intent, ticker=ticker, reason="llm:test")


def _mock_client(intent: str, ticker: str | None = None) -> MagicMock:
    """Mock LLM client whose tool_calls return the given intent."""
    tc = MagicMock()
    tc.input = {"intent": intent, "ticker": ticker, "reason": "test mock"}
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    client = MagicMock()
    client.generate.return_value = resp
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# llm_classify — tool-call parsing
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_classify_parses_tool_call():
    client = _mock_client("price_action", "MBB")
    r = llm_classify("MBB money flow today", client=client)
    assert r is not None
    assert r.intent == "price_action"
    assert r.ticker == "MBB"
    assert r.reason.startswith("llm:")


def test_llm_classify_normalises_invalid_intent():
    tc = MagicMock()
    tc.input = {"intent": "INVALID", "reason": "bad"}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "conversation"


def test_llm_classify_out_of_scope_passthrough():
    """out_of_scope is a valid intent — must not be coerced to conversation."""
    client = _mock_client("out_of_scope", None)
    r = llm_classify("bitcoin price", client=client)
    assert r is not None
    assert r.intent == "out_of_scope"


def test_llm_classify_text_scan_fallback():
    resp = MagicMock()
    resp.tool_calls = []
    resp.text = "this should route to macro_sector based on content"
    client = MagicMock(); client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "macro_sector"


def test_llm_classify_text_scan_first_match_wins():
    from agents.classifier import INTENTS
    first = INTENTS[0]   # "price_action"
    second = INTENTS[1]  # "technical_analysis"
    resp = MagicMock(); resp.tool_calls = []
    resp.text = f"this is {second} but also {first} content"
    client = MagicMock(); client.generate.return_value = resp
    r = llm_classify("query", client=client)
    assert r is not None
    assert r.intent == first


def test_llm_classify_empty_text_returns_none():
    resp = MagicMock(); resp.tool_calls = []; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    assert llm_classify("some query", client=client) is None


def test_llm_classify_exception_returns_none():
    client = MagicMock()
    client.generate.side_effect = ConnectionError("network error")
    assert llm_classify("some query", client=client) is None


def test_llm_classify_whitespace_ticker_normalised():
    client = _mock_client("macro_sector", "   ")
    r = llm_classify("oil prices impact", client=client)
    assert r is not None
    assert r.ticker is None


def test_llm_classify_missing_intent_key_defaults_conversation():
    tc = MagicMock()
    tc.input = {"reason": "no intent key"}  # no "intent"
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    r = llm_classify("some query", client=client)
    assert r is not None
    assert r.intent == "conversation"


def test_llm_classify_none_ticker_preserved():
    tc = MagicMock()
    tc.input = {"intent": "market_brief", "reason": "market question"}  # no "ticker"
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    r = llm_classify("vnindex today", client=client)
    assert r is not None
    assert r.intent == "market_brief"
    assert r.ticker is None


# ═══════════════════════════════════════════════════════════════════════════════
# llm_classify — generate() call shape + prompt
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_classify_generate_call_shape():
    from agents.classifier import _TOOL
    from llm.types import Message

    resp = MagicMock(); resp.tool_calls = []; resp.text = "conversation"
    client = MagicMock(); client.generate.return_value = resp

    llm_classify("test query here", client=client)

    client.generate.assert_called_once()
    kwargs = client.generate.call_args.kwargs
    assert kwargs["tools"] == [_TOOL]
    assert kwargs["max_tokens"] == 256
    msgs = kwargs["messages"]
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "test query here"


def test_llm_classify_system_prompt_contains_all_intents():
    from agents.classifier import _SYSTEM, INTENTS
    for intent in INTENTS:
        assert intent in _SYSTEM, f"intent '{intent}' missing from system prompt"


# ═══════════════════════════════════════════════════════════════════════════════
# classify_hybrid — LLM-only behavior
# ═══════════════════════════════════════════════════════════════════════════════

def test_classify_hybrid_returns_llm_result():
    with patch("agents.classifier.llm_classify", return_value=_make_result("investment_case", "HPG")):
        r = classify_hybrid("Is HPG worth buying?")
    assert r.intent == "investment_case"
    assert r.ticker == "HPG"


def test_classify_hybrid_falls_back_to_conversation_on_none():
    with patch("agents.classifier.llm_classify", return_value=None):
        r = classify_hybrid("some ambiguous query")
    assert r.intent == "conversation"
    assert r.ticker is None


def test_classify_hybrid_out_of_scope_passthrough():
    with patch("agents.classifier.llm_classify", return_value=_make_result("out_of_scope", None)):
        r = classify_hybrid("bitcoin price")
    assert r.intent == "out_of_scope"


def test_classify_hybrid_forwards_messages():
    history = [{"role": "user", "content": "phân tích HPG"}]
    with patch("agents.classifier.llm_classify", return_value=_make_result("technical_analysis", "HPG")) as m:
        classify_hybrid("phân tích sâu hơn", messages=history)
    m.assert_called_once()
    assert m.call_args.kwargs.get("messages") == history
