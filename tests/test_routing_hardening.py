"""
tests/test_routing_hardening.py — Regression guards for plan items #12, #13, 2.1, 2.2.

  Unit (fast, no LLM/network):
    - llm_route out_of_scope tool → direct text decline, no fallback classify (2.1)
    - llm_route injects last_intent/last_subject into system prompt (#12)
    - _read_prior / _snapshot_ts — stale vs fresh interrupt, checkpoint-ts fallback (#13)
    - budget guard: route_after_subqueries / route_after_critique exit early (2.2)

Run:
    pytest tests/test_routing_hardening.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════════
# 2.1 — out_of_scope tool (LLM decides, no deterministic heuristic)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_route_out_of_scope_tool(monkeypatch):
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
    classify.assert_not_called()  # no fallback that would remap foreign → VN intent


def test_llm_route_tools_include_out_of_scope(monkeypatch):
    """Router sends all three tools to the LLM."""
    from agents import conversation_router as cr
    from agents.classifier import RouterResult
    resp = MagicMock(); resp.tool_calls = []; resp.text = "xin chào"
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        cr.llm_route("xin chào", [], "sys", client=client)
    tools = client.generate.call_args.kwargs["tools"]
    names = {t["name"] for t in tools}
    assert names == {"needs_agent_run", "direct_reply", "out_of_scope"}


# ═══════════════════════════════════════════════════════════════════════════════
# #12 — last_intent / last_subject injection
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_route_injects_last_context(monkeypatch):
    """Follow-up context is passed explicitly into the system prompt."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB"])
    tc = MagicMock()
    tc.name = "needs_agent_run"
    tc.input = {"intent": "technical_analysis", "ticker": "HPG", "query": "phân tích sâu hơn HPG", "reason": ""}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp

    route = cr.llm_route(
        "phân tích sâu hơn", [], "sys", client=client,
        last_intent="technical_analysis", last_subject="HPG",
    )

    system = client.generate.call_args.kwargs["system"]
    assert "Lượt trước" in system
    assert "technical_analysis" in system
    assert "HPG" in system
    assert route["type"] == "agent"


def test_llm_route_no_last_context_no_injection(monkeypatch):
    """No prior context → system prompt passed through unchanged."""
    from agents import conversation_router as cr
    from agents.classifier import RouterResult
    resp = MagicMock(); resp.tool_calls = []; resp.text = "xin chào"
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("conversation", None, "x")):
        cr.llm_route("xin chào", [], "sys", client=client)
    assert client.generate.call_args.kwargs["system"] == "sys"


# ═══════════════════════════════════════════════════════════════════════════════
# #13 — stale interrupt detection
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
    assert prior["last_intent"] == "technical_analysis"
    assert prior["last_subject"] == "HPG"


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


def test_read_prior_no_interrupt():
    from memory.turn_handler import _read_prior
    snap = _FakeSnap(values={"intent": "valuation", "ticker": "VCB"}, next_=())
    prior = _read_prior(_FakeApp(snap), {})
    assert prior["interrupted"] is False
    assert prior["stale"] is False
    assert prior["last_intent"] == "valuation"
    assert prior["last_subject"] == "VCB"


def test_snapshot_ts_checkpoint_fallback():
    """created_at absent → fall back to checkpoint['ts'] ISO string."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# 2.2 — per-turn budget guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_budget_exceeded_on_llm_calls():
    from agents.graph import _llm_budget_exceeded
    import time
    assert _llm_budget_exceeded({"llm_calls": 100}) is True
    # wall-clock exceeded: turn started long ago
    assert _llm_budget_exceeded({"llm_calls": 0, "turn_started_at": time.time() - 1000}) is True


def test_budget_exceeded_false_within_budget():
    from agents.graph import _llm_budget_exceeded
    import time
    assert _llm_budget_exceeded({"llm_calls": 1, "turn_started_at": time.time()}) is False


def test_route_after_subqueries_skips_replan_on_budget():
    from agents.graph import route_after_subqueries
    state = {"llm_calls": 100, "sub_results_empty_ratio": 1.0, "replan_attempted": False}
    assert route_after_subqueries(state) == "synthesize"


def test_route_after_critique_saves_on_budget():
    from agents.graph import route_after_critique
    state = {"llm_calls": 100, "critique_pass": False, "critique_attempts": 0}
    assert route_after_critique(state) == "save"


# ═══════════════════════════════════════════════════════════════════════════════
# 2.1 — out_of_scope in classifier fallback + graph (LLM misroutes foreign subject)
# ═══════════════════════════════════════════════════════════════════════════════

def test_fallback_classify_out_of_scope_returns_text(monkeypatch):
    from agents.conversation_router import _fallback_classify
    from agents.classifier import RouterResult
    with patch("agents.classifier.classify_hybrid",
               return_value=RouterResult("out_of_scope", None, "x")):
        route = _fallback_classify("giá bitcoin hôm nay", [])
    assert route is not None
    assert route["type"] == "text"
    assert route["reason"] == "out_of_scope"


def test_llm_route_needs_agent_out_of_scope_intent_declined(monkeypatch):
    """LLM returns needs_agent_run with intent='out_of_scope' → declined, not agent."""
    from agents import conversation_router as cr
    from agents.classifier import RouterResult
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


def test_check_conversation_out_of_scope():
    from agents.graph import check_conversation
    assert check_conversation({"intent": "out_of_scope"}) == "out_of_scope"
    assert check_conversation({"intent": "conversation"}) == "skip"
    assert check_conversation({"intent": "technical_analysis"}) == "verify"


def test_route_after_clarify_out_of_scope():
    from agents.graph import _route_after_clarify
    assert _route_after_clarify({"intent": "out_of_scope", "ticker": ""}) == "out_of_scope"


def test_node_out_of_scope_returns_decline():
    from agents.graph import node_out_of_scope
    from agents.classifier import OUT_OF_SCOPE_REPLY
    out = node_out_of_scope({})
    assert out["report"] == OUT_OF_SCOPE_REPLY
    assert out["intent"] == "out_of_scope"
