"""Unit tests for mixed-intent execution in turn_handler (memory/turn_handler.py).

Router-level `decompose` parsing lives in tests/test_router_intents_cache.py; these tests
cover the turn_handler side — building agent state per segment and executing segments.
"""

from unittest.mock import MagicMock


class _FakeApp:
    def __init__(self, report: str = "RPT"):
        self.report = report
        self.invocations: list = []

    def invoke(self, state, config):
        self.invocations.append((state, config))
        return {"report": self.report}


def test_build_agent_state_sets_fields():
    from memory.turn_handler import _build_agent_state

    route = {
        "query": "giá HPG", "intent": "price_action", "ticker": "HPG",
        "tickers": ["HPG"], "sector": "",
        "time_context": {"anchor": "2026-09-05"},
    }
    st = _build_agent_state(route, "giá HPG", "c", "u", "t", [])

    assert st["intent"] == "price_action"
    assert st["ticker"] == "HPG"
    assert st["tickers"] == ["HPG"]
    assert st["original_query"] == "giá HPG"
    assert st["time_context"] == {"anchor": "2026-09-05"}


def test_mixed_parts_executes_segments():
    from memory.turn_handler import _mixed_parts

    app = _FakeApp("RPT")
    segs = [
        {"kind": "general", "text": "hello"},
        {"kind": "agent", "intent": "price_action", "ticker": "HPG",
         "query": "giá HPG", "tickers": ["HPG"], "sector": "", "time_context": None},
        {"kind": "out_of_scope", "text": "decline"},
    ]
    parts = _mixed_parts(
        app, segs, "msg", "conv", "u", "t", [],
        {"configurable": {"thread_id": "conv"}},
    )

    assert parts == ["hello", "RPT", "decline"]
    assert len(app.invocations) == 1


def test_mixed_parts_extra_agent_uses_isolated_thread():
    from memory.turn_handler import _mixed_parts

    app = _FakeApp("RPT")
    segs = [
        {"kind": "agent", "intent": "price_action", "ticker": "HPG",
         "query": "giá HPG", "tickers": ["HPG"], "sector": "", "time_context": None},
        {"kind": "agent", "intent": "price_action", "ticker": "VCB",
         "query": "giá VCB", "tickers": ["VCB"], "sector": "", "time_context": None},
    ]
    parts = _mixed_parts(
        app, segs, "msg", "conv", "u", "t", [],
        {"configurable": {"thread_id": "conv"}},
    )

    assert parts == ["RPT", "RPT"]
    # second agent segment runs on an isolated thread, not the main one
    assert app.invocations[0][1]["configurable"]["thread_id"] == "conv"
    assert app.invocations[1][1]["configurable"]["thread_id"] == "conv:mix1"
