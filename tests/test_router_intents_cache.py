"""
tests/test_router_intents_cache.py — Unified suite: router, intents, llm_route, tools, cache.

One file consolidating every test for routing, intent classification, entry routing, tool
wiring, and the intent-level cache — both fast unit tests (no LLM/network) and end-to-end
tests (real DeepSeek LLM + real tools/DB/Redis). Supersedes:

  - tests/test_router_intents_e2e.py    (e2e: classify / llm_route / gather / graph / stream)
  - tests/test_graph_unified.py         (unit: graph routing nodes, fan-out, approval)
  - tests/test_routing_enhancement.py   (unit: llm_route fail-safes, ticker extraction, cache scope)
  - tests/test_routing_hardening.py     (unit: out_of_scope tool, follow-up context, budget guard)
  - tests/test_hybrid_router.py         (unit: llm_classify / classify_hybrid parsing)
  - tests/test_bai32_cache.py           (unit + e2e: intent-level cache key / TTL / roundtrip)

Run unit only (fast, no LLM/network):
    python -m pytest tests/test_router_intents_cache.py -v -m "not e2e"

Run e2e only (slow, real LLM + external APIs + Redis):
    python -m pytest tests/test_router_intents_cache.py -v -s -m e2e

Prereqs (e2e): Postgres + Redis running, .env with LLM_PROVIDER=deepseek + key.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

# Graph nodes print Vietnamese text; Windows default console is cp1252, so a `print`
# inside decompose_node would raise UnicodeEncodeError and abort the turn.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

from agents.classifier import RouterResult, classify_hybrid, llm_classify
from agents.graph import (
    _is_empty_result,
    _route_after_clarify,
    route_after_subqueries,
    route_after_critique,
    _gather_tickers,
    run_subqueries_node,
    _check_approval_decision,
    _request_approval,
    build_single_subtask_node,
    _is_label_line,
    _strip_subresult_labels,
)
from agents.state import make_initial_state, AgentState
from agents.focus import Focus, resolve_focus
from core.tickers import raw_tickers, extract_tickers

# Minimal persona for llm_route — routing is tool-driven, not prompt-sensitive.
_ROUTE_SYSTEM = (
    "Bạn là trợ lý phân tích tài chính chứng khoán Việt Nam. "
    "Trả lời bằng tiếng Việt."
)

_SKIP = object()    # sentinel: "don't assert ticker" (vs None = "assert missing")
_MISSING = object()  # sentinel for "ticker not passed" vs "ticker=None"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse(lines: list[str]) -> dict[str, list[dict]]:
    """Group SSE blocks into {event: [data_dict, ...]}.

    Each `lines` element is one SSE block (stream_turn yields blocks ending in ``\n\n``).
    A bare `data:` block (no `event:`) is a "message" event; the event name resets per
    block so a preceding `event: status` never swallows the text chunks that follow.
    """
    events: dict[str, list[dict]] = {}
    for raw in lines:
        event = "message"
        for s in raw.split("\n"):
            s = s.strip()
            if s.startswith("event: "):
                event = s[len("event: "):].strip()
            elif s.startswith("data: "):
                try:
                    events.setdefault(event, []).append(json.loads(s[len("data: "):].strip()))
                except Exception:
                    continue
    return events


def _routing(events: dict) -> dict | None:
    """Return the post-graph routing status event (has 'agent')."""
    for p in events.get("status", []):
        if p.get("step") == "routing" and p.get("agent"):
            return p
    return None


def _done(events: dict) -> dict | None:
    d = events.get("done")
    return d[0] if d else None


def _reply(events: dict) -> str:
    return "".join(p.get("text", "") for p in events.get("message", []))


def _status(events: dict, step: str) -> dict | None:
    """Return the first status payload whose step == `step` (e.g. cache_hit)."""
    for p in events.get("status", []):
        if p.get("step") == step:
            return p
    return None


def _run_stream(conversation_id: str, user_id: str, message: str, is_first_turn: bool = False) -> list[str]:
    from memory.turn_handler import stream_turn

    lines: list[str] = []

    async def _go():
        async for line in stream_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=message,
            tenant_id="default",
            is_first_turn=is_first_turn,
        ):
            lines.append(line)

    asyncio.run(_go())
    return lines


def _invoke(query: str, conversation_id: str = "", tenant_id: str = "default") -> dict:
    """Invoke the full graph — classify_node handles intent/ticker internally."""
    from agents.graph import build_graph

    app = build_graph()
    state = make_initial_state(query, conversation_id=conversation_id, tenant_id=tenant_id)
    return app.invoke(state)


def _new_conv():
    from memory.conversation import create_conversation
    uid = f"test-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(uid, "default")
    return cid, uid


def _state(query: str, intent: str = "", ticker: str | None = _MISSING, **kw) -> AgentState:
    """Build a state dict with intent/ticker set directly (for node-level unit tests)."""
    s = make_initial_state(query, **kw)
    if intent:
        s["intent"] = intent
    if ticker is not _MISSING:
        s["ticker"] = ticker  # None means genuinely missing; "" means resolved-but-empty
    return s


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


# ── Parametrized tables (e2e) ─────────────────────────────────────────────────

# (intent, prompt, ticker) — ticker=_SKIP means "don't assert ticker";
# ticker=None means "assert ticker is None"; a str asserts equality.
CLASSIFY_CASES = [
    ("price_action",       "Khối ngoại mua bán ròng HPG hôm nay thế nào?", "HPG"),
    ("price_action",       "Khối lượng giao dịch HPG có đột biến không?", _SKIP),
    ("price_action",       "Dòng tiền vào HPG hôm nay",                   _SKIP),
    ("technical_analysis", "RSI và MACD của FPT đang như thế nào?",       "FPT"),
    ("technical_analysis", "Vùng hỗ trợ kháng cự của VNM ở đâu?",         "VNM"),
    ("technical_analysis", "Xu hướng kỹ thuật HPG tuần này",              _SKIP),
    ("technical_analysis", "Phân tích HPG",                               "HPG"),
    ("technical_analysis", "Phân tích kỹ thuật HPG hôm nay",              "HPG"),
    ("technical_analysis", "Chỉ số kỹ thuật của FPT tuần này",            "FPT"),
    ("technical_analysis", "Phân tích VNM hôm nay",                       "VNM"),
    ("valuation",          "P/E của HPG hiện tại so với trung bình ngành thế nào?", "HPG"),
    ("valuation",          "P/E của HPG so với trung bình ngành thép là bao nhiêu?", "HPG"),
    ("valuation",          "ROE của VCB năm ngoái là bao nhiêu?",         _SKIP),
    ("rag_qa",             "Doanh thu HPG năm 2024 là bao nhiêu?",        "HPG"),
    ("rag_qa",             "Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?", "HPG"),
    ("macro_sector",       "Tỷ giá USD/VND hôm nay ảnh hưởng gì đến FPT?", _SKIP),
    ("macro_sector",       "Giá thép HRC thế giới tăng ảnh hưởng HPG thế nào?", _SKIP),
    ("macro_sector",       "Dầu Brent hôm nay giá bao nhiêu?",            _SKIP),
    ("macro_sector",       "Tỷ giá USD/VND hôm nay và giá dầu thô thế nào?", None),
    ("news_sentiment",     "Tin tức về HPG trong 3 ngày gần nhất",        "HPG"),
    ("news_sentiment",     "Sentiment của cộng đồng về VNM như thế nào?", _SKIP),
    ("news_sentiment",     "Diễn đàn đang nói gì về HPG?",                _SKIP),
    ("investment_case",    "HPG có nên mua không?",                       "HPG"),
    ("investment_case",    "Khuyến nghị VCB lúc này: mua bán hay nắm giữ?", "VCB"),
    ("investment_case",    "Tổng kết FPT — bull case và bear case",       "FPT"),
    ("investment_case",    "MWG đáng đầu tư không?",                      "MWG"),
    ("investment_case",    "Phân tích toàn diện HPG",                     "HPG"),
    ("investment_case",    "Bull case và bear case của FPT là gì?",       _SKIP),
    ("investment_case",    "Khuyến nghị VCB",                             _SKIP),
    ("screening",          "Top 5 mã có ROE cao nhất trong DB",           _SKIP),
    ("screening",          "Lọc cổ phiếu ngành chứng khoán đang tích lũy", _SKIP),
    ("screening",          "Tìm cổ phiếu có RSI < 40 và P/E < 10",        _SKIP),
    ("screening",          "Lọc cổ phiếu có ROE > 20%",                   _SKIP),
    ("breakout_scan",      "Quét cổ phiếu đang breakout tạo đỉnh mới",    None),
    ("market_brief",       "Thị trường chứng khoán hôm nay thế nào?",     None),
    ("market_brief",       "VNINDEX đang ở đâu?",                         None),
    ("market_brief",       "VNINDEX đang ở mức nào?",                     None),
    ("conversation",       "Xin chào bạn tên gì",                         None),
]

# (query, expected_intent, expected_ticker). intent=None → type="text" (direct reply);
# ticker="" → assert routed ticker is ""; ticker=None → don't assert ticker.
ROUTE_CASES = [
    ("Khối ngoại mua bán ròng HPG hôm nay thế nào?",       "price_action",       "HPG"),
    ("RSI và MACD của HPG đang như thế nào?",              "technical_analysis", "HPG"),
    ("P/E của HPG so với trung bình ngành thép là bao nhiêu?", "valuation",      "HPG"),
    ("So sánh BID và CTG",                                 "valuation",          "BID"),
    ("Tỷ giá USD/VND hôm nay và giá dầu thô thế nào?",     "macro_sector",       ""),
    ("Tổng quan thị trường chứng khoán hôm nay",           "market_brief",       ""),
    ("Tin tức về HPG trong 3 ngày gần nhất",               "news_sentiment",     "HPG"),
    ("HPG có nên mua không? Cho bull case và bear case",   "investment_case",    "HPG"),
    ("Top 5 mã có ROE cao nhất trong database",            "screening",          None),
    ("Lọc cổ phiếu có RSI dưới 30",                        "screening",          None),
    ("Lọc cổ phiếu có P/E dưới 10 và ROE trên 20",         "screening",          None),
    ("Quét cổ phiếu đang breakout tạo đỉnh mới",           "breakout_scan",      None),
    ("Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?",  "rag_qa",             "HPG"),
    ("Xin chào, bạn tên gì?",                              None,                 None),
]

# `marker` is the deterministic header each gather_data() prepends.
GATHER_CASES = [
    ("price_action",       "Khối ngoại mua bán ròng HPG hôm nay thế nào?",       "HPG", "[GIÁ & DÒNG TIỀN"),
    ("technical_analysis", "RSI và MACD của HPG đang như thế nào?",               "HPG", "[KỸ THUẬT"),
    ("valuation",          "P/E của HPG so với trung bình ngành thép là bao nhiêu?", "HPG", "[CƠ BẢN & ĐỊNH GIÁ"),
    ("macro_sector",       "Tỷ giá USD/VND hôm nay và giá dầu thô thế nào?",      None,  "[VĨ MÔ & NGÀNH]"),
    ("news_sentiment",     "Tin tức về HPG trong 3 ngày gần nhất",                "HPG", "[TIN TỨC & SENTIMENT"),
    ("investment_case",    "HPG có nên mua không? Cho bull case và bear case",    "HPG", "[GIÁ & DÒNG TIỀN"),
    ("screening",          "Top 5 mã có ROE cao nhất trong database",             None,  "[SCREENING]"),
    ("breakout_scan",      "Quét cổ phiếu đang breakout tạo đỉnh mới",            None,  "[BREAKOUT"),
]

STREAM_CASES = [
    ("giá và dòng tiền HPG hôm nay",              "price_action"),
    ("phân tích kỹ thuật HPG RSI MACD",           "technical_analysis"),
    ("doanh thu lợi nhuận HPG năm 2024",           "rag_qa"),
    ("P/E của HPG so với trung bình ngành thép?", "valuation"),
    ("tỷ giá USD/VND và giá thép hôm nay",        "macro_sector"),
    ("tin tức về HPG trong 3 ngày gần nhất",       "news_sentiment"),
    ("HPG có nên mua không?",                      "investment_case"),
    ("top 5 mã ROE cao nhất",                      "screening"),
    ("quét cổ phiếu đang breakout tạo đỉnh mới",   "breakout_scan"),
    ("tổng quan thị trường chứng khoán hôm nay",  "market_brief"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — classifier (agents/classifier.py)
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


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — entry router (agents/conversation_router.py)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_route_out_of_scope_tool():
    """out_of_scope tool call → direct text decline, no re-classify."""
    from agents import conversation_router as cr
    tc = MagicMock()
    tc.name = "out_of_scope"
    tc.input = {"text": "Tôi chỉ hỗ trợ chứng khoán VN.", "reason": "crypto"}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    with patch("agents.classifier.classify_hybrid") as classify:
        route = cr.llm_route("giá bitcoin hôm nay", [], "sys", client=client)

    assert route["type"] == "text"
    assert route["text"] == "Tôi chỉ hỗ trợ chứng khoán VN."
    assert route["reason"] == "crypto"
    classify.assert_not_called()


def test_llm_route_tools_include_out_of_scope():
    from agents import conversation_router as cr
    resp = MagicMock(); resp.tool_calls = []; resp.text = "xin chào"
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        cr.llm_route("xin chào", [], "sys", client=client)
    tools = client.generate.call_args.kwargs["tools"]
    names = {t["name"] for t in tools}
    assert names == {"needs_agent_run", "direct_reply", "out_of_scope", "decompose"}


def test_llm_route_injects_last_context(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "technical_analysis", "ticker": "HPG", "query": "phân tích sâu hơn HPG", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "phân tích sâu hơn", [], "sys", client=client,
        focus=Focus(tickers=["HPG"], intent="technical_analysis", query="phân tích kỹ thuật HPG"),
    )

    system = client.generate.call_args.kwargs["system"]
    assert "Lượt trước" in system
    assert "technical_analysis" in system
    assert "HPG" in system
    assert route["type"] == "agent"


def test_llm_route_nonbare_followup_suppresses_intent(monkeypatch):
    """A follow-up naming its own action must NOT inject the prior intent — only the subject."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["VCB"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "price_action", "ticker": "VCB", "query": "giá cổ phiếu vietcombank?", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "giá cổ phiếu vietcombank?", [], "sys", client=client,
        focus=Focus(tickers=["VCB"], intent="valuation", query="P/E của VCB"),
    )

    system = client.generate.call_args.kwargs["system"]
    assert "chủ thể 'VCB'" in system
    assert "intent 'valuation'" not in system, "prior intent must be suppressed for a non-bare follow-up"
    assert route["type"] == "agent"


def test_llm_route_fresh_ticker_not_inherited(monkeypatch):
    """Continuation phrase + NEW ticker → fresh, prior intent/subject must NOT leak in."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "technical_analysis", "ticker": "HPG", "query": "phân tích thêm HPG", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "phân tích thêm HPG", [], "sys", client=client,
        focus=Focus(tickers=["VCB"], intent="valuation", query="P/E của VCB"),
    )

    system = client.generate.call_args.kwargs["system"]
    assert "Lượt trước" not in system, "a fresh-ticker message must not inject prior context"
    assert route["type"] == "agent"
    assert route["intent"] != "valuation", "prior valuation intent must not leak to HPG"
    assert route["ticker"] == "HPG"
    assert route["tickers"] == ["HPG"]


def test_llm_route_continue_carries_all_tickers(monkeypatch):
    """Bare continuation after a comparison keeps BOTH tickers, not just the first."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["BID", "CTG"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "valuation", "ticker": "BID", "query": "phân tích sâu hơn BID", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "phân tích sâu hơn", [], "sys", client=client,
        focus=Focus(tickers=["BID", "CTG"], intent="valuation", query="so sánh BID và CTG"),
    )

    system = client.generate.call_args.kwargs["system"]
    assert "BID, CTG" in system, "both prior tickers must be injected"
    assert route["type"] == "agent"
    assert route["intent"] == "valuation"
    assert route["tickers"] == ["BID", "CTG"], "continuation must carry the full ticker list"


def test_llm_route_sector_continuation_injects_subject(monkeypatch):
    """Bare continuation after a sector turn injects the sector subject (not just intent)."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "macro_sector", "ticker": "", "query": "phân tích sâu hơn ngành ngân hàng", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "phân tích sâu hơn", [], "sys", client=client,
        focus=Focus(tickers=[], sector="ngân hàng", intent="macro_sector", query="phân tích ngành ngân hàng"),
    )

    system = client.generate.call_args.kwargs["system"]
    assert "chủ thể 'ngân hàng'" in system, "sector subject must be injected for a sector continuation"
    assert "macro_sector" in system
    assert route["type"] == "agent"
    assert route["sector"] == "ngân hàng"


def test_fallback_classify_continue_inherits(monkeypatch):
    """_fallback_classify on a bare continuation with focus → inherits intent + tickers."""
    from agents.conversation_router import _fallback_classify
    focus = Focus(tickers=["BID", "CTG"], intent="valuation", query="so sánh BID và CTG")
    route = _fallback_classify("phân tích sâu hơn", [], focus=focus)
    assert route is not None
    assert route["type"] == "agent"
    assert route["intent"] == "valuation"
    assert route["tickers"] == ["BID", "CTG"]
    assert route["ticker"] == "BID"


def test_llm_route_no_last_context_no_injection():
    from agents import conversation_router as cr
    resp = MagicMock(); resp.tool_calls = []; resp.text = "xin chào"
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        cr.llm_route("xin chào", [], "sys", client=client)
    system = client.generate.call_args.kwargs["system"]
    assert "Lượt trước" not in system
    assert system.endswith("sys")


def test_router_time_context_schema_and_parse():
    from agents.conversation_router import AGENT_RUN_TOOL, _parse_time_context
    props = AGENT_RUN_TOOL["input_schema"]["properties"]
    assert "time_context" in props, "needs_agent_run must expose time_context"

    tc = _parse_time_context({"start_date": "2026-08-29", "end_date": "2026-09-05", "explicit": True})
    assert tc["start_date"] == "2026-08-29"
    assert tc["end_date"] == "2026-09-05"
    assert tc["explicit"] is True
    assert tc["anchor"], "anchor must be set"

    tc2 = _parse_time_context(None)
    assert tc2["start_date"] is None
    assert tc2["explicit"] is False
    assert tc2["end_date"] == tc2["anchor"]

    # explicit derives from start_date, not the LLM's `explicit` flag: a real start_date
    # with explicit=false must force True (else "tuần trước"/"tháng trước" collide on the
    # same cache key), and explicit=true with no start_date must force False (no spurious
    # "..<today>" day-scope).
    tc3 = _parse_time_context({"start_date": "2026-08-29", "explicit": False})
    assert tc3["start_date"] == "2026-08-29"
    assert tc3["explicit"] is True, "real start_date must force explicit=True"

    tc4 = _parse_time_context({"start_date": None, "explicit": True})
    assert tc4["start_date"] is None
    assert tc4["explicit"] is False, "no start_date must force explicit=False"


def test_llm_route_screening_single_filter(monkeypatch):
    """Screening filter flows through llm_route → RouteResult (single filter)."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "HSG", "NKG"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {
        "intent": "screening", "ticker": "", "query": "lọc RSI dưới 30 ngành thép",
        "screening": [{"indicator": "rsi", "op": "<", "threshold": 30}], "reason": "screening",
    }
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route("lọc RSI dưới 30 ngành thép", [], "sys", client=client)

    assert route["type"] == "agent"
    assert route["intent"] == "screening"
    assert route["sector"] == "thép"
    assert route["screening"] == [{"indicator": "rsi", "op": "<", "threshold": 30}]


def test_llm_route_screening_multi_filter_and(monkeypatch):
    """Multiple filters AND-ed flow through llm_route."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "HSG", "NKG"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {
        "intent": "screening", "ticker": "", "query": "lọc ROE > 20 và RSI < 30 ngành thép",
        "screening": [
            {"indicator": "roe", "op": ">", "threshold": 20},
            {"indicator": "rsi", "op": "<", "threshold": 30},
        ],
        "reason": "screening",
    }
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route("lọc ROE > 20 và RSI < 30 ngành thép", [], "sys", client=client)

    assert route["type"] == "agent"
    assert route["intent"] == "screening"
    assert route["screening"] == [
        {"indicator": "roe", "op": ">", "threshold": 20},
        {"indicator": "rsi", "op": "<", "threshold": 30},
    ]


def test_llm_route_screening_overrides_decompose(monkeypatch):
    """'lọc RSI < 30' misrouted to decompose → deterministic screening agent route."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["VCB", "BID", "CTG"])
    tc = MagicMock()
    tc.name = "decompose"
    tc.input = {"segments": [{"kind": "agent", "intent": "technical_analysis",
                              "ticker": "", "query": "lọc RSI dưới 30 ngành ngân hàng", "reason": ""}]}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route("lọc RSI dưới 30 ngành ngân hàng", [], "sys", client=client)

    assert route["type"] == "agent"
    assert route["intent"] == "screening"
    assert route["sector"] == "ngân hàng"
    assert route["screening"] == [{"indicator": "rsi", "op": "<", "threshold": 30}]


def test_llm_route_comparison_does_not_inherit_prior(monkeypatch):
    """REPRO: 'so sánh ACB, VNM, FPT' after 'so sánh VCB, TCB' must NOT inherit VCB/TCB.

    The LLM is mocked to HALLUCINATE ticker=VCB (copied from the prior turn), so this
    isolates whether the deterministic entities override in _parse_agent_fields wins.
    """
    from agents import conversation_router as cr
    from agents.focus import Focus
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["VCB", "TCB", "ACB", "VNM", "FPT"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "valuation", "ticker": "VCB", "query": "so sánh VCB và TCB", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "so sánh ACB, VNM, FPT", [], "sys", client=client,
        focus=Focus(tickers=["VCB", "TCB"], intent="valuation", query="so sánh VCB và TCB"),
    )
    assert route["type"] == "agent"
    assert route["tickers"] == ["ACB", "VNM", "FPT"], f"got {route['tickers']!r} — inherited prior VCB/TCB"


def test_classifier_screening_schema():
    from agents.classifier import _TOOL
    assert "screening" in _TOOL["input_schema"]["properties"], "classify_intent must expose screening"


def test_looks_like_screening_guard():
    from agents.graph import _looks_like_screening
    assert _looks_like_screening("lọc RSI < 30")
    assert _looks_like_screening("lọc cổ phiếu ngành thép có ROE > 20")
    assert not _looks_like_screening("phân tích kỹ thuật HPG")
    assert not _looks_like_screening("giá cổ phiếu VCB hôm nay")


def test_screening_gather_lambda_not_shadowed():
    """screening lambda must call the MODULE's gather_data, not a shadowed param."""
    from agents.graph import _get_gather_map
    from agents.intents import screening as screening_mod
    fn = _get_gather_map()["screening"]
    f = [{"indicator": "rsi", "op": "<", "threshold": 30}]
    with patch.object(screening_mod, "gather_data", return_value="[SCREENING] ok") as m:
        out = fn("", "lọc RSI < 30", None, sf=f)
    assert out == "[SCREENING] ok"
    assert m.call_args.kwargs["screening"] == f


def test_llm_route_llm_error_returns_text():
    """LLM exception → short text error, NOT market_brief."""
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
    """direct_reply misused for a financial query → re-classified to agent."""
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


def test_fallback_classify_out_of_scope_returns_text():
    from agents.conversation_router import _fallback_classify
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("out_of_scope", None, "x")):
        route = _fallback_classify("giá bitcoin hôm nay", [])
    assert route is not None
    assert route["type"] == "text"
    assert route["reason"] == "out_of_scope"


def test_llm_route_needs_agent_out_of_scope_intent_declined():
    """LLM returns needs_agent_run with intent='out_of_scope' → declined, not agent."""
    from agents import conversation_router as cr
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "out_of_scope", "ticker": "", "query": "bitcoin", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("out_of_scope", None, "x")):
        route = cr.llm_route("giá bitcoin hôm nay", [], "sys", client=client)
    assert route["type"] == "text"
    assert route["reason"] == "out_of_scope"


def test_llm_route_out_of_scope_empty_text_still_declines():
    """out_of_scope tool with empty text → OUT_OF_SCOPE_REPLY, never a blank reply."""
    from agents import conversation_router as cr
    from agents.classifier import OUT_OF_SCOPE_REPLY
    tc = MagicMock(); tc.name = "out_of_scope"; tc.input = {"text": "", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    route = cr.llm_route("giá bitcoin hôm nay", [], "sys", client=client)
    assert route["type"] == "text"
    assert route["text"] == OUT_OF_SCOPE_REPLY


def test_llm_route_direct_reply_empty_text_not_blank():
    """direct_reply with empty text on a social turn → safe non-empty text, not ''."""
    from agents import conversation_router as cr
    tc = MagicMock(); tc.name = "direct_reply"; tc.input = {"text": "", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        route = cr.llm_route("cảm ơn bạn", [], "sys", client=client)
    assert route["type"] == "text"
    assert route["text"], "empty direct_reply text must not escape as blank"


# ── Mixed-intent decomposition (decompose tool) ───────────────────────────────

def _decompose_client(segments):
    tc = MagicMock(); tc.name = "decompose"
    tc.input = {"segments": segments, "reason": "mixed test"}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    return client


def test_llm_route_decompose_general_plus_agent(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["FPT"])
    client = _decompose_client([
        {"kind": "general", "text": "Mình vẫn khỏe, cảm ơn bạn!"},
        {"kind": "agent", "intent": "investment_case", "ticker": "FPT",
         "query": "FPT có nên mua không?", "reason": ""},
    ])
    route = cr.llm_route("hôm nay bạn thế nào? À, FPT có nên mua không?", [], "sys", client=client)

    assert route["type"] == "mixed"
    segs = route["segments"]
    assert [s["kind"] for s in segs] == ["general", "agent"]
    assert segs[0]["text"] == "Mình vẫn khỏe, cảm ơn bạn!"
    assert segs[1]["intent"] == "investment_case"
    assert segs[1]["ticker"] == "FPT"
    assert segs[1]["tickers"] == ["FPT"]
    assert segs[1]["query"] == "FPT có nên mua không?"


def test_llm_route_decompose_supported_plus_out_of_scope(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["FPT"])
    client = _decompose_client([
        {"kind": "agent", "intent": "investment_case", "ticker": "FPT",
         "query": "phân tích FPT", "reason": ""},
        {"kind": "out_of_scope", "text": "Tôi không hỗ trợ đặt lệnh mua/bán.",
         "reason": "trade_execution"},
    ])
    route = cr.llm_route("phân tích FPT và đặt lệnh mua giúp tôi", [], "sys", client=client)

    assert route["type"] == "mixed"
    segs = route["segments"]
    assert [s["kind"] for s in segs] == ["agent", "out_of_scope"]
    assert segs[1]["text"] == "Tôi không hỗ trợ đặt lệnh mua/bán."


def test_llm_route_decompose_out_of_scope_empty_text_falls_back():
    from agents import conversation_router as cr
    from agents.classifier import OUT_OF_SCOPE_REPLY
    client = _decompose_client([
        {"kind": "out_of_scope", "text": "", "reason": "trade_execution"},
    ])
    route = cr.llm_route("đặt lệnh mua bitcoin giúp tôi", [], "sys", client=client)

    assert route["type"] == "mixed"
    assert route["segments"][0]["text"] == OUT_OF_SCOPE_REPLY


def test_llm_route_decompose_general_empty_skipped():
    from agents import conversation_router as cr
    client = _decompose_client([
        {"kind": "general", "text": "", "reason": ""},
        {"kind": "out_of_scope", "text": "decline", "reason": ""},
    ])
    route = cr.llm_route("...", [], "sys", client=client)

    assert route["type"] == "mixed"
    assert [s["kind"] for s in route["segments"]] == ["out_of_scope"]


def test_llm_route_decompose_agent_bad_ticker_dropped(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["VCB"])
    client = _decompose_client([
        {"kind": "agent", "intent": "price_action", "ticker": "AAPL",
         "query": "giá AAPL", "reason": ""},
    ])
    route = cr.llm_route("giá AAPL thế nào?", [], "sys", client=client)

    assert route["type"] == "mixed"
    seg = route["segments"][0]
    assert seg["ticker"] == ""
    assert "ticker_dropped=AAPL" in seg["reason"]


def test_llm_route_decompose_no_usable_segments_falls_back():
    from agents import conversation_router as cr
    client = _decompose_client([])
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        route = cr.llm_route("hôm nay bạn thế nào?", [], "sys", client=client)
    assert route["type"] == "text"


def test_parse_agent_fields_needs_agent_run_unchanged(monkeypatch):
    """Regression guard: extracting _parse_agent_fields must not change needs_agent_run."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock(); tc.name = "needs_agent_run"
    tc.input = {"intent": "technical_analysis", "ticker": "HPG",
                "query": "phân tích HPG", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route("phân tích HPG", [], "sys", client=client)

    assert route["type"] == "agent"
    assert route["intent"] == "technical_analysis"
    assert route["ticker"] == "HPG"
    assert route["tickers"] == ["HPG"]
    assert route["query"] == "phân tích HPG"


def test_validate_ticker(monkeypatch):
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    from agents.conversation_router import _validate_ticker
    assert _validate_ticker("hpg") == "HPG"
    assert _validate_ticker(" NGANHANG ") == ""
    assert _validate_ticker("") == ""
    assert _validate_ticker("AAPL") == ""
    assert _validate_ticker("VNINDEX") == ""


def test_validate_ticker_shape_fallback_when_table_down(monkeypatch):
    """get_tickers() raises (DB down) → 3-letter shape check, not silent drop to ''."""
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("core.tickers.get_tickers", _boom)
    from agents.conversation_router import _validate_ticker
    assert _validate_ticker("HPG") == "HPG"
    assert _validate_ticker("hpg") == "HPG"
    assert _validate_ticker("AAPL") == ""      # 4 letters — not the VN 3-char shape
    assert _validate_ticker("VNINDEX") == ""   # index, not a 3-char ticker
    assert _validate_ticker("") == ""


def test_fallback_classify_passes_history():
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
    # Fallback classify must attach a default time_context so classify_node doesn't
    # burn a second LLM call in _resolve_time_context.
    assert route.get("time_context") is not None
    assert route["time_context"]["explicit"] is False
    assert route["time_context"]["start_date"] is None


def test_fallback_classify_social_returns_none():
    from agents.conversation_router import _fallback_classify
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        assert _fallback_classify("cảm ơn bạn", []) is None


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — ticker extraction + cache key scope (core/tickers.py, core/cache.py)
# ═══════════════════════════════════════════════════════════════════════════════

def test_raw_tickers_uppercase_first():
    assert raw_tickers("phân tích HPG và VCB") == ["HPG", "VCB"]
    assert raw_tickers("hpg vcb") == ["HPG", "VCB"]
    assert raw_tickers("tin tức về HPG") == ["HPG"]


def test_raw_tickers_stopwords():
    assert raw_tickers("ROE của HPG") == ["HPG"]
    assert raw_tickers("EPS và P/B của VCB") == ["VCB"]


def test_extract_tickers_false_positive_universe(monkeypatch):
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB", "TIN", "NAM", "HAI"])
    assert extract_tickers("phân tích HPG và VCB") == ["HPG", "VCB"]
    assert extract_tickers("tin tức về HPG") == ["HPG"]
    assert extract_tickers("TIN và HPG") == ["TIN", "HPG"]


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — graph routing nodes (agents/graph.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitRouteAfterClarify:
    def test_market_brief(self):
        assert _route_after_clarify(_state("thị trường hôm nay", intent="market_brief", ticker="")) == "market_brief"

    def test_simple_leaf_intent_with_ticker(self):
        assert _route_after_clarify(_state("RSI HPG", intent="technical_analysis", ticker="HPG")) == "simple"

    def test_complex_intent_always_decompose(self):
        assert _route_after_clarify(_state("mua HPG không", intent="investment_case", ticker="HPG")) == "decompose"

    def test_no_intent_decompose(self):
        assert _route_after_clarify(_state("HPG")) == "decompose"


GOLDEN_ROUTES = [
    ("market_brief",       "",     "market_brief"),
    ("technical_analysis", "HPG",  "simple"),
    ("valuation",          "HPG",  "simple"),
    ("price_action",       "HPG",  "simple"),
    ("investment_case",    "HPG",  "decompose"),
    ("macro_sector",       "",     "decompose"),
    ("screening",          "",     "simple"),
    ("breakout_scan",      "",     "simple"),
    ("conversation",       "",     "conversation"),
]


@pytest.mark.parametrize("intent,ticker,expected", GOLDEN_ROUTES)
def test_route_after_clarify_golden(intent, ticker, expected):
    assert _route_after_clarify({"intent": intent, "ticker": ticker}) == expected


def test_route_after_clarify_no_intent_decompose():
    assert _route_after_clarify({"intent": "", "ticker": ""}) == "decompose"


def test_route_after_clarify_out_of_scope():
    assert _route_after_clarify({"intent": "out_of_scope", "ticker": ""}) == "out_of_scope"


def test_check_conversation_out_of_scope():
    from agents.graph import check_conversation
    assert check_conversation({"intent": "out_of_scope"}) == "out_of_scope"
    assert check_conversation({"intent": "conversation"}) == "skip"
    assert check_conversation({"intent": "technical_analysis"}) == "verify"


def test_node_out_of_scope_returns_decline():
    from agents.graph import node_out_of_scope
    from agents.classifier import OUT_OF_SCOPE_REPLY
    out = node_out_of_scope({})
    assert out["report"] == OUT_OF_SCOPE_REPLY
    assert out["intent"] == "out_of_scope"


class TestUnitLoopRouting:
    def test_is_empty_result(self):
        assert _is_empty_result("") is True
        assert _is_empty_result("[NEWS_SENTIMENT — lỗi: max recursion]") is True
        assert _is_empty_result("ticker không được rỗng.") is True
        assert _is_empty_result("Doanh thu HPG Q1: 100 tỷ") is False

    def test_replan_when_mostly_empty(self):
        assert route_after_subqueries({"sub_results_empty_ratio": 0.9}) == "replan"

    def test_no_replan_after_attempt(self):
        assert route_after_subqueries({"sub_results_empty_ratio": 0.9, "replan_attempted": True}) == "synthesize"

    def test_synthesize_when_data_present(self):
        assert route_after_subqueries({"sub_results_empty_ratio": 0.0}) == "synthesize"

    def test_critique_save_on_pass(self):
        assert route_after_critique({"critique_pass": True}) == "save"

    def test_critique_retry_once(self):
        assert route_after_critique({"critique_pass": False, "critique_attempts": 0}) == "retry"
        assert route_after_critique({"critique_pass": False, "critique_attempts": 1}) == "retry"
        assert route_after_critique({"critique_pass": False, "critique_attempts": 2}) == "save"


def test_budget_exceeded_on_llm_calls():
    from agents.graph import _llm_budget_exceeded
    assert _llm_budget_exceeded({"llm_calls": 100}) is True
    assert _llm_budget_exceeded({"llm_calls": 0, "turn_started_at": time.time() - 1000}) is True


def test_budget_exceeded_false_within_budget():
    from agents.graph import _llm_budget_exceeded
    assert _llm_budget_exceeded({"llm_calls": 1, "turn_started_at": time.time()}) is False


def test_route_after_subqueries_skips_replan_on_budget():
    assert route_after_subqueries({"llm_calls": 100, "sub_results_empty_ratio": 1.0, "replan_attempted": False}) == "synthesize"


def test_route_after_critique_saves_on_budget():
    assert route_after_critique({"llm_calls": 100, "critique_pass": False, "critique_attempts": 0}) == "save"


def test_is_label_line():
    assert _is_label_line("[PRICE_ACTION — VCB]") is True
    assert _is_label_line("[GIÁ & DÒNG TIỀN VCB]") is True
    assert _is_label_line("Giá: 100") is False
    assert _is_label_line("") is False


def test_strip_subresult_labels_single_drops_outer_and_question():
    block = (
        "[PRICE_ACTION — VCB]\n"
        "cho tôi phân tích tổng hợp về giá cổ phiếu VCB\n"
        "[GIÁ & DÒNG TIỀN VCB]\n"
        "Giá: 100\nKhối lượng: 200"
    )
    out = _strip_subresult_labels([block])
    assert "[PRICE_ACTION" not in out, "outer [INTENT — TICKER] label must be stripped"
    assert "cho tôi phân tích tổng hợp" not in out, "single sub-task must drop the rewritten question line"
    assert "[GIÁ & DÒNG TIỀN VCB]" in out, "inner [MARKER] must be kept as the source label"
    assert "Giá: 100" in out
    assert "Khối lượng: 200" in out


def test_strip_subresult_labels_multi_keeps_question_and_markers():
    b1 = "[TECHNICAL_ANALYSIS — VCB]\nphân tích kỹ thuật VCB\n[KỸ THUẬT VCB]\nRSI: 55"
    b2 = "[NEWS_SENTIMENT — VCB]\ntin tức VCB\n[TIN TỨC & SENTIMENT VCB]\n- bài 1"
    out = _strip_subresult_labels([b1, b2])
    assert "[TECHNICAL_ANALYSIS" not in out, "outer label must be stripped"
    assert "[NEWS_SENTIMENT" not in out, "outer label must be stripped"
    assert "phân tích kỹ thuật VCB" in out, "multi sub-task must keep the question line (source angle)"
    assert "tin tức VCB" in out
    assert "[KỸ THUẬT VCB]" in out, "inner source marker must be kept for decompose"
    assert "[TIN TỨC & SENTIMENT VCB]" in out, "inner source marker must be kept for decompose"
    assert "RSI: 55" in out
    assert "- bài 1" in out


class TestUnitGatherAndApproval:
    def test_gather_tickers_multi_merged_in_order(self):
        fn = lambda t, q: f"[{t}] data"
        out = _gather_tickers(fn, ["HPG", "VCB"], "so sánh")
        assert "[HPG] data" in out and "[VCB] data" in out
        assert out.index("[HPG]") < out.index("[VCB]"), "input order must be preserved"

    def test_gather_tickers_single_short_circuits(self):
        fn = lambda t, q: f"[{t}] data"
        assert _gather_tickers(fn, ["HPG"], "q") == "[HPG] data"

    def test_gather_tickers_empty_returns_empty(self):
        assert _gather_tickers(lambda t, q: "x", [], "q") == ""

    def test_run_subqueries_unknown_intent_falls_back_macro(self, monkeypatch):
        from agents import graph as G
        calls = {"n": 0}

        def macro_fn(t, q):
            calls["n"] += 1
            return "[VĨ MÔ data]"

        monkeypatch.setattr(G, "_get_gather_map", lambda: {"macro_sector": macro_fn})
        out = G.run_subqueries_node({
            "sub_tasks": [{"intent": "bogus_intent", "tickers": [], "question": "x"}],
            "query": "x", "ticker": "",
        })

        assert calls["n"] == 1, "unknown intent must fall back to macro_sector"
        assert out["sub_results"] and "[MACRO_SECTOR" in out["sub_results"][0]

    def test_run_subqueries_empty_sets_replan_note(self, monkeypatch):
        from agents import graph as G
        monkeypatch.setattr(G, "_get_gather_map", lambda: {"macro_sector": lambda t, q: ""})
        out = G.run_subqueries_node({
            "sub_tasks": [{"intent": "macro_sector", "tickers": [], "question": "x"}],
            "query": "x", "ticker": "",
        })
        assert out["sub_results_empty_ratio"] == 1.0
        assert out.get("replan_note"), "mostly-empty results must trigger replan_note"

    def test_run_subqueries_replanned_does_not_set_note_again(self, monkeypatch):
        from agents import graph as G
        monkeypatch.setattr(G, "_get_gather_map", lambda: {"macro_sector": lambda t, q: ""})
        out = G.run_subqueries_node({
            "sub_tasks": [{"intent": "macro_sector", "tickers": [], "question": "x"}],
            "query": "x", "ticker": "",
            "replan_attempted": True,
        })
        assert "replan_note" not in out, "already replanned → no second replan_note"

    def test_approval_decision_end_on_reject(self):
        assert _check_approval_decision({"error": "rejected_by_user"}) == "end"
        assert _check_approval_decision({}) == "synthesize_final"

    def test_request_approval_reject(self, monkeypatch):
        monkeypatch.setattr("langgraph.types.interrupt", lambda proposal: False)
        assert _request_approval({}) == {"error": "rejected_by_user"}

    def test_request_approval_approve(self, monkeypatch):
        monkeypatch.setattr("langgraph.types.interrupt", lambda proposal: True)
        assert _request_approval({}) == {}

    def test_fast_path_prefers_original_multi_ticker(self, monkeypatch):
        monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
        out = build_single_subtask_node({
            "intent": "valuation",
            "ticker": "HPG",
            "query": "P/E HPG",
            "original_query": "so sánh P/E HPG và VCB",
        })
        assert out["sub_tasks"][0]["intent"] == "valuation"
        assert out["sub_tasks"][0]["tickers"] == ["HPG", "VCB"]
        assert out["sub_tasks"][0]["question"] == "so sánh P/E HPG và VCB"

    def test_fast_path_stored_tickers_recovers_question(self, monkeypatch):
        """Router carried tickers=[HPG,VCB] but LLM query dropped VCB → question recovers."""
        monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
        out = build_single_subtask_node({
            "intent": "valuation",
            "ticker": "HPG",
            "tickers": ["HPG", "VCB"],      # router carried full list
            "query": "P/E HPG",              # LLM rewrite dropped VCB
            "original_query": "so sánh P/E HPG và VCB",
        })
        st = out["sub_tasks"][0]
        assert st["tickers"] == ["HPG", "VCB"]
        assert st["question"] == "so sánh P/E HPG và VCB", "question must recover both tickers"

    def test_fast_path_single_ticker(self, monkeypatch):
        monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
        out = build_single_subtask_node({
            "intent": "technical_analysis",
            "ticker": "HPG",
            "query": "RSI HPG",
            "original_query": "RSI HPG",
        })
        assert out["sub_tasks"][0]["tickers"] == ["HPG"]


def test_valuation_gather_explicit_tickers_compare(monkeypatch):
    """Root fix: valuation uses the router's tickers list, not query re-parse → no sector peers."""
    from agents.intents import fundamentals as f
    monkeypatch.setattr(f, "_fetch_valuation", lambda t: {"ticker": t})
    monkeypatch.setattr(f, "_build_analysis", lambda ticker, rows: f"analysis({len(rows)})")
    out = f.gather_data("HPG", "so sánh HPG và VCB", tickers=["HPG", "VCB"])
    assert "[SO SÁNH HPG & VCB]" in out
    assert "analysis(2)" in out, "must compare exactly 2 tickers, not sector peers"


def test_build_agent_state_resets_critique_loop():
    """Multi-turn bug: critique-loop state must NOT leak from the prior turn's checkpoint."""
    from memory.turn_handler import _build_agent_state
    route = {"intent": "valuation", "ticker": "ACB", "tickers": ["ACB", "VNM"],
             "query": "so sánh ACB và VNM", "sector": "", "screening": None,
             "time_context": None}
    state = _build_agent_state(route, "so sánh ACB, VNM", "cid", "u", "t", [])
    assert state["critique_attempts"] == 0
    assert state["critique_feedback"] == ""
    assert state["critique_pass"] is True
    assert state["report_candidates"] == []
    assert state["critique_results"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — stale interrupt detection (memory/turn_handler.py)
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeSnap:
    def __init__(self, values=None, next_=None, created_at=None, checkpoint=None):
        self.values = values or {}
        self.next = next_
        self.created_at = created_at
        self.checkpoint = checkpoint


class _FakeApp:
    def __init__(self, snap):
        self.snap = snap
    def get_state(self, cfg):
        return self.snap


def test_read_prior_fresh_interrupt_not_stale():
    from memory.turn_handler import _read_prior
    snap = _FakeSnap(
        values={"intent": "technical_analysis", "ticker": "HPG"},
        next_=("clarify_node",),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    prior = _read_prior(_FakeApp(snap), {})
    assert prior["interrupted"] is True
    assert prior["stale"] is False
    assert prior["focus"].intent == "technical_analysis"
    assert prior["focus"].tickers == ["HPG"]


def test_read_prior_stale_interrupt():
    from memory.turn_handler import _read_prior
    snap = _FakeSnap(
        values={"intent": "", "ticker": ""},
        next_=("clarify_node",),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=700),
    )
    prior = _read_prior(_FakeApp(snap), {})
    assert prior["interrupted"] is True
    assert prior["stale"] is True
    assert prior["focus"] is None


def test_read_prior_no_interrupt():
    from memory.turn_handler import _read_prior
    snap = _FakeSnap(values={"intent": "valuation", "ticker": "VCB"}, next_=())
    prior = _read_prior(_FakeApp(snap), {})
    assert prior["interrupted"] is False
    assert prior["stale"] is False
    assert prior["focus"].intent == "valuation"
    assert prior["focus"].tickers == ["VCB"]


def test_read_prior_comparison_tickers_survive(monkeypatch):
    """A comparison turn's 2nd/3rd ticker survives via original_query, even though the
    top-level `ticker` only stored the first one."""
    from memory.turn_handler import _read_prior
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["BID", "CTG"])
    snap = _FakeSnap(
        values={"intent": "valuation", "ticker": "BID", "original_query": "so sánh BID và CTG"},
        next_=(),
    )
    prior = _read_prior(_FakeApp(snap), {})
    assert prior["focus"].tickers == ["BID", "CTG"]


def test_snapshot_ts_checkpoint_fallback():
    from memory.turn_handler import _snapshot_ts
    snap = _FakeSnap(created_at=None, checkpoint={"ts": "2026-09-05T03:00:00+00:00"})
    ts = _snapshot_ts(snap)
    assert ts == datetime(2026, 9, 5, 3, 0, 0, tzinfo=timezone.utc)


def test_snapshot_ts_naive_assumed_utc():
    from memory.turn_handler import _snapshot_ts
    snap = _FakeSnap(created_at=datetime(2026, 9, 5, 3, 0, 0))
    ts = _snapshot_ts(snap)
    assert ts.tzinfo is not None
    assert ts.utcoffset().total_seconds() == 0


def test_snapshot_ts_string_created_at():
    """LangGraph >=0.3 returns created_at as an ISO string — parse it, not .replace()."""
    from memory.turn_handler import _snapshot_ts
    snap = _FakeSnap(created_at="2026-09-05T03:00:00+00:00")
    ts = _snapshot_ts(snap)
    assert ts == datetime(2026, 9, 5, 3, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — intent classification (real LLM)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
@pytest.mark.parametrize(
    "intent,prompt,ticker",
    CLASSIFY_CASES,
    ids=[f"{intent}[{i}]" for i, (intent, _, _) in enumerate(CLASSIFY_CASES)],
)
def test_classify_intent(intent: str, prompt: str, ticker):
    r = classify_hybrid(prompt)
    assert r.intent == intent, f"[{intent}] classified as {r.intent!r}: {r.reason} | {prompt!r}"
    if ticker is _SKIP:
        pass
    elif ticker is None:
        assert r.ticker is None, f"[{intent}] ticker={r.ticker!r}, expected None | {prompt!r}"
    else:
        assert r.ticker == ticker, f"[{intent}] ticker={r.ticker!r}, expected {ticker!r} | {prompt!r}"
    print(f"\n  [{intent}] ticker={r.ticker!r} reason={r.reason!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — entry routing llm_route (real LLM)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
@pytest.mark.parametrize(
    "query,expected_intent,expected_ticker",
    ROUTE_CASES,
    ids=[f"{intent or 'text'}[{i}]" for i, (_, intent, _) in enumerate(ROUTE_CASES)],
)
def test_llm_route_golden(query, expected_intent, expected_ticker):
    from agents.conversation_router import llm_route
    route = llm_route(query, [], _ROUTE_SYSTEM)
    if expected_intent is None:
        assert route["type"] == "text", f"expected direct reply, got {route}"
        return
    assert route["type"] == "agent", f"expected agent, got {route}"
    assert route["intent"] == expected_intent, f"got {route['intent']!r}, expected {expected_intent!r}"
    if expected_ticker is not None:
        assert route["ticker"] == expected_ticker, f"got {route['ticker']!r}, expected {expected_ticker!r}"
    print(f"\n  [{expected_intent}] route ticker={route.get('ticker')!r}")


@pytest.mark.e2e
def test_llm_route_out_of_scope_declines():
    from agents.conversation_router import llm_route
    route = llm_route("giá bitcoin hôm nay thế nào?", [], _ROUTE_SYSTEM)
    assert route["type"] == "text", f"expected decline, got {route}"
    assert route.get("text"), "decline text must be non-empty"
    assert route.get("reason"), "decline must carry a reason"
    print(f"\n[out_of_scope] reason={route.get('reason')!r} text={route.get('text')!r}")


@pytest.mark.e2e
def test_llm_route_e2e_prior_fpt_new_vcb_is_fresh():
    """Prior turn = FPT; user now names VCB → FRESH. VCB must not inherit FPT's focus."""
    from agents.conversation_router import llm_route
    focus = Focus(tickers=["FPT"], intent="technical_analysis", query="phân tích kỹ thuật FPT")
    route = llm_route("giá VCB hôm nay thế nào?", [], _ROUTE_SYSTEM, focus=focus)
    assert route["type"] == "agent", f"expected agent, got {route}"
    assert route["ticker"] == "VCB", f"expected fresh VCB, got {route.get('ticker')!r}"
    assert route["tickers"] == ["VCB"], f"prior FPT must be replaced: {route.get('tickers')!r}"
    print(f"\n[prior FPT → ask VCB] ticker={route.get('ticker')!r} tickers={route.get('tickers')!r}")


@pytest.mark.e2e
def test_llm_route_e2e_prior_fpt_bare_continuation_inherits():
    """Prior turn = FPT; bare continuation → inherit FPT (contrast to the fresh case above)."""
    from agents.conversation_router import llm_route
    focus = Focus(tickers=["FPT"], intent="technical_analysis", query="phân tích kỹ thuật FPT")
    route = llm_route("phân tích sâu hơn", [], _ROUTE_SYSTEM, focus=focus)
    assert route["type"] == "agent", f"expected agent, got {route}"
    assert route["ticker"] == "FPT", f"bare continuation must inherit FPT, got {route.get('ticker')!r}"
    assert route["tickers"] == ["FPT"], f"got {route.get('tickers')!r}"
    print(f"\n[prior FPT → 'sâu hơn'] ticker={route.get('ticker')!r}")


@pytest.mark.e2e
def test_llm_route_e2e_screening_extracts_filter():
    """screening with a numeric filter → llm_route extracts the structured filter array."""
    from agents.conversation_router import llm_route
    route = llm_route("Lọc RSI dưới 30 ngành thép", [], _ROUTE_SYSTEM)
    assert route["type"] == "agent", f"expected agent, got {route}"
    assert route["intent"] == "screening", f"got {route['intent']!r}"
    filters = route.get("screening") or []
    rsi = next((f for f in filters if f.get("indicator") == "rsi"), None)
    assert rsi, f"expected an rsi filter in {filters!r}"
    assert rsi.get("op") == "<" and rsi.get("threshold") == 30, f"got {rsi!r}"
    print(f"\n[screening] filters={filters!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — tools + data (real DB / external APIs, no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
@pytest.mark.parametrize("intent,prompt,ticker,marker", GATHER_CASES, ids=[c[0] for c in GATHER_CASES])
def test_gather_data_returns_correct_tool_data(intent, prompt, ticker, marker):
    from agents.graph import _get_gather_map
    fn = _get_gather_map()[intent]
    data = fn(ticker or "", prompt)
    assert data, f"[{intent}] gather returned empty string"
    assert marker in data, (
        f"[{intent}] gather missing {marker!r} — wrong tool fired or error stub. Got: {data[:200]!r}"
    )
    print(f"\n  [{intent}] gather {len(data)} chars: {data[:120]!r}")


@pytest.mark.e2e
def test_rag_qa_gather_returns_context():
    from agents.graph import _get_gather_map
    data = _get_gather_map()["rag_qa"]("HPG", "Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?")
    assert isinstance(data, str) and len(data.strip()) > 10, f"rag_qa gather empty: {data!r}"
    print(f"\n  [rag_qa] gather {len(data)} chars: {data[:120]!r}")


@pytest.mark.e2e
def test_valuation_gather_cross_ticker():
    from agents.intents.fundamentals import gather_data
    data = gather_data("HPG", "So sánh P/E của HPG với VCB")
    assert "[SO SÁNH" in data, f"cross-ticker marker missing: {data[:200]!r}"
    assert "VCB" in data
    print(f"\n  [valuation cross] {data[:120]!r}")


@pytest.mark.e2e
def test_market_brief_graph_produces_report():
    from agents.market_brief_graph import build_brief_graph, make_initial_state as _mbi
    app = build_brief_graph()
    final = app.invoke(_mbi(date=str(date.today()), output_path=""))
    report = final.get("report_text", "")
    assert report, "market_brief report_text empty"
    assert "📰" in report, "market brief template header missing"
    assert "NHẬN ĐỊNH" in report, "market brief outlook section missing"
    print(f"\n  [market_brief] {len(report)} chars, missing_fields={final.get('missing_fields')}")


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — graph.invoke() directly, all intent nodes (real LLM + tools)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestGraphInvoke:
    def test_price_action(self):
        final = _invoke("giá và dòng tiền HPG hôm nay")
        report = final.get("report", "")
        print(f"\n[price_action] report[:300]: {report[:300]}")
        assert not final.get("needs_clarification")
        assert len(report) > 50
        assert any(kw in report.lower() for kw in ["hpg", "giá", "khối lượng", "dòng tiền", "price"])

    def test_technical_analysis(self):
        final = _invoke("phân tích kỹ thuật HPG: RSI, MACD, xu hướng")
        report = final.get("report", "")
        print(f"\n[technical] report[:300]: {report[:300]}")
        assert len(report) > 100
        assert any(kw in report.lower() for kw in ["rsi", "macd", "xu hướng", "hỗ trợ", "kháng cự", "ema", "sma"])

    def test_rag_qa(self):
        final = _invoke("doanh thu HPG năm 2024 bao nhiêu?")
        report = final.get("report", "")
        print(f"\n[rag_qa] report[:300]: {report[:300]}")
        assert len(report) > 20

    def test_valuation(self):
        final = _invoke("P/E của HPG so với trung bình ngành thép là bao nhiêu?")
        report = final.get("report", "")
        print(f"\n[valuation] report[:300]: {report[:300]}")
        assert not final.get("needs_clarification")
        assert len(report) > 50
        assert any(kw in report.lower() for kw in ["p/e", "pe", "định giá", "ngành", "hpg"])

    def test_macro_sector(self):
        final = _invoke("tỷ giá USD/VND và giá thép hôm nay")
        report = final.get("report", "")
        print(f"\n[macro_sector] report[:300]: {report[:300]}")
        assert len(report) > 50

    def test_news_sentiment(self):
        final = _invoke("tin tức về HPG trong 3 ngày gần nhất")
        report = final.get("report", "")
        print(f"\n[news_sentiment] report[:300]: {report[:300]}")
        assert len(report) > 50

    def test_investment_case(self):
        final = _invoke("HPG có nên mua không? Bull case và bear case")
        report = final.get("report", "")
        print(f"\n[investment_case] report[:500]: {report[:500]}")
        assert len(report) > 200
        assert any(kw in report.lower() for kw in ["bull", "bear", "khuyến nghị", "mua", "bán", "nắm giữ"])

    def test_screening(self):
        final = _invoke("top 5 mã ROE cao nhất trong database")
        report = final.get("report", "")
        print(f"\n[screening] report[:300]: {report[:300]}")
        assert len(report) > 20

    def test_breakout_scan(self):
        final = _invoke("quét cổ phiếu đang breakout tạo đỉnh mới")
        report = final.get("report", "")
        print(f"\n[breakout_scan] report[:300]: {report[:300]}")
        assert len(report) > 20

    def test_market_brief(self):
        final = _invoke("tổng quan thị trường chứng khoán hôm nay")
        report = final.get("report", "")
        print(f"\n[market_brief] report[:400]: {report[:400]}")
        assert len(report) > 100
        assert any(kw in report.lower() for kw in ["vnindex", "vn-index", "thị trường", "vn30", "hsx"])

    def test_bctc_keywords_to_rag_qa(self):
        final = _invoke("báo cáo tài chính HPG quý 1 2025")
        report = final.get("report", "")
        print(f"\n[knowledge] report[:300]: {report[:300]}")
        assert not final.get("needs_clarification")
        assert len(report) > 50

    def test_ticker_with_keyword_no_clarification(self):
        final = _invoke("phân tích kỹ thuật HPG RSI")
        report = final.get("report", "")
        print(f"\n[technical+keyword] intent=%s report[:200]: %s" % (final.get("intent"), report[:200]))
        assert not final.get("needs_clarification")
        assert len(report) > 50

# ═══════════════════════════════════════════════════════════════════════════════
# E2E — full stream_turn path (real LLM + tools → SSE)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestStreamAllIntents:
    @pytest.mark.parametrize("query,expected_intent", STREAM_CASES)
    def test_stream_intent(self, query, expected_intent):
        cid, uid = _new_conv()
        events = _parse_sse(_run_stream(cid, uid, query))
        routing = _routing(events)
        done = _done(events)
        reply = _reply(events)

        print(f"\n[{expected_intent}] routing={routing}, reply[:200]={reply[:200]}")

        assert routing is not None, f"{expected_intent}: routing event missing"
        assert routing.get("agent") == expected_intent, \
            f"expected {expected_intent}, got {routing.get('agent')}: {routing}"
        assert done is not None, f"{expected_intent}: done event missing"
        assert len(reply) > 20, f"{expected_intent}: reply too short ({len(reply)})"

    def test_conversation(self):
        """Conversation is a direct text reply (type=text) — no agent graph, no routing event."""
        cid, uid = _new_conv()
        events = _parse_sse(_run_stream(cid, uid, "xin chào, bạn là ai?"))
        done = _done(events)
        reply = _reply(events)
        print(f"\n[conversation] reply={reply[:200]}, done={done}")
        assert done is not None, "conversation must emit done"
        assert len(reply) > 10, "conversation must stream a reply"

    def test_out_of_scope(self):
        cid, uid = _new_conv()
        events = _parse_sse(_run_stream(cid, uid, "giá bitcoin hôm nay thế nào?"))
        routing = _routing(events)
        reply = _reply(events)
        print(f"\n[out_of_scope] routing={routing}, reply={reply[:200]}")
        assert reply.strip(), "decline text must be streamed"
        assert routing is None, "out_of_scope must not route to an agent"

    def test_investment_case_sections(self):
        cid, uid = _new_conv()
        events = _parse_sse(_run_stream(cid, uid, "HPG có nên mua không? Cho mình bull case và bear case"))
        routing = _routing(events)
        reply = _reply(events)
        print(f"\n[investment_case] routing={routing}")
        print(f"[investment_case] reply[:500]: {reply[:500]}")
        assert routing is not None
        assert routing.get("agent") == "investment_case"
        reply_lower = reply.lower()
        assert any(kw in reply_lower for kw in ["bull", "bear", "luận điểm", "khuyến nghị"]), \
            "missing bull/bear/recommendation section"
        assert any(kw in reply_lower for kw in ["mua", "bán", "nắm giữ", "tích lũy"]), \
            "missing buy/sell/hold verdict"

@pytest.mark.e2e
class TestStreamClarificationResume:
    def test_clarification_then_resume(self):
        cid, uid = _new_conv()

        events_n = _parse_sse(_run_stream(cid, uid, "phân tích kỹ thuật"))
        reply_n = _reply(events_n)
        print(f"\n[clarification turn N] reply: {reply_n[:200]}")
        assert len(reply_n) > 10
        assert any(kw in reply_n.lower() for kw in ["mã", "ticker", "cổ phiếu", "công ty"]), \
            f"clarification must ask for ticker, got: {reply_n}"

        events_n1 = _parse_sse(_run_stream(cid, uid, "HPG"))
        routing_n1 = _routing(events_n1)
        done_n1 = _done(events_n1)
        reply_n1 = _reply(events_n1)
        print(f"\n[resume turn N+1] routing: {routing_n1}")
        print(f"[resume turn N+1] reply[:300]: {reply_n1[:300]}")

        assert done_n1 is not None
        assert len(reply_n1) > 50
        if routing_n1 and routing_n1.get("agent"):
            assert routing_n1.get("agent") in (
                "technical_analysis", "price_action", "rag_qa", "investment_case"
            ), f"unexpected intent after resume: {routing_n1}"


# ═══════════════════════════════════════════════════════════════════════════════
# E2E — sub-query pipeline regressions (real LLM / real tools)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
def test_run_subqueries_dedupes_news_fetch_via_tool_cache(monkeypatch):
    from agents import graph as G
    from tools import price as price_mod

    calls = {"n": 0}

    def spy(ticker, days):
        calls["n"] += 1

    monkeypatch.setattr(price_mod, "_auto_fetch_ticker_news", spy)

    state = {
        "sub_tasks": [
            {"intent": "news_sentiment", "tickers": ["VCB"], "question": f"tin VCB {i}"}
            for i in range(4)
        ],
        "query": "Tin tức VCB",
        "ticker": "VCB",
    }
    G.run_subqueries_node(state)
    assert calls["n"] <= 2, f"news auto-fetch fired {calls['n']}×, expected ≤2 (cache-deduped)"


@pytest.mark.e2e
def test_decompose_assigns_distinct_intents():
    from agents.graph import decompose_node
    out = decompose_node({
        "query": (
            "Phân tích toàn diện VCB: kỹ thuật (RSI, MACD), "
            "định giá so ngành ngân hàng, và khuyến nghị nên mua hay bán"
        ),
        "intent": "investment_case",
        "ticker": "VCB",
    })
    sub_tasks = out["sub_tasks"]
    assert sub_tasks, "decompose produced no sub-tasks"
    intents = {t["intent"] for t in sub_tasks}
    assert len(intents) >= 2, (
        f"all sub-tasks share intent {intents!r} — expected ≥2 distinct intents. "
        f"sub_tasks={sub_tasks!r}"
    )


@pytest.mark.e2e
def test_ngan_hang_thinh_vuong_resolves_vpb():
    result = classify_hybrid("phân tích cổ phiếu Ngân hàng Thịnh Vượng")
    print(f"\nRouter result: intent={result.intent} ticker={result.ticker} reason={result.reason}")
    assert result.ticker == "VPB", f"Expected VPB, got {result.ticker}"


