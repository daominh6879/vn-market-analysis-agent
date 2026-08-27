"""
tests/test_agent_integration.py — Real end-to-end tests for bài 22 + 23.

No mocks. Calls real LLM via create_client(), real price/news tools.
Requires: .env with LLM_PROVIDER + API key, network access.

Run:
    pytest tests/test_agent_integration.py -v -s
"""

from __future__ import annotations

import pytest

from agents.graph import build_graph
from agents.planner import MAX_BUDGET_TOKENS, default_plan, generate_plan, validate_plan
from agents.state import make_initial_state
from llm.factory import create_client
from tools.registry import TOOL_REGISTRY

REGISTRY = set(TOOL_REGISTRY.keys())


# ══════════════════════════════════════════════════════════════════════════════
# BÀI 22 — Sequential graph, real run
# ══════════════════════════════════════════════════════════════════════════════

class TestBai22RealGraph:
    @pytest.fixture(scope="class")
    def app(self):
        return build_graph()

    def _run(self, app, query: str) -> dict:
        state = make_initial_state(query)
        return app.invoke(state)

    def test_hpg_report_not_empty(self, app):
        result = self._run(app, "HPG")
        assert result.get("report"), "Report is empty for HPG"

    def test_hpg_report_has_markdown_structure(self, app):
        result = self._run(app, "HPG")
        report = result["report"]
        assert "#" in report, "Report has no markdown headings"

    def test_hpg_risk_verdict_set(self, app):
        result = self._run(app, "HPG")
        verdict = result.get("risk_verdict", "")
        assert verdict, "risk_verdict not set"
        assert any(kw in verdict for kw in ("OK", "HIGH_VOLATILITY", "INSUFFICIENT_DATA")), (
            f"Unexpected risk_verdict: {verdict}"
        )

    def test_fpt_does_not_crash(self, app):
        result = self._run(app, "FPT")
        # Either report or error — must not raise
        assert result.get("report") or result.get("error") is not None

    def test_market_query_detection(self, app):
        state = make_initial_state("Phân tích thị trường hôm nay")
        assert state["is_market_query"] is True
        result = app.invoke(state)
        assert result.get("report"), "Market query returned empty report"

    def test_step_count_equals_four(self, app):
        result = self._run(app, "VNM")
        assert result.get("step_count") == 4, (
            f"Expected 4 steps, got {result.get('step_count')}"
        )

    def test_history_has_synthesize_entry(self, app):
        result = self._run(app, "MWG")
        history = result.get("history", [])
        steps = [h.get("step") for h in history]
        assert "synthesize" in steps, f"synthesize not in history: {steps}"

    def test_synthesize_records_tokens(self, app):
        result = self._run(app, "TCB")
        history = result.get("history", [])
        synth = next((h for h in history if h.get("step") == "synthesize"), None)
        assert synth is not None
        assert synth.get("input_tokens", 0) > 0, "No input tokens recorded"
        assert synth.get("output_tokens", 0) > 0, "No output tokens recorded"


# ══════════════════════════════════════════════════════════════════════════════
# BÀI 23 — generate_plan real LLM, validate_plan real registry
# ══════════════════════════════════════════════════════════════════════════════

SIMPLE_QUERY = "Giá HPG hôm nay là bao nhiêu?"
MEDIUM_QUERY = "Phân tích kỹ thuật HPG trong 14 ngày gần nhất"
COMPLEX_QUERY = "So sánh HPG và FPT: kỹ thuật, rủi ro biến động, tin tức gần nhất"


class TestBai23RealPlanning:
    @pytest.fixture(scope="class")
    def client(self):
        return create_client()

    def test_simple_query_valid_plan(self, client):
        plan, errors = generate_plan(SIMPLE_QUERY, REGISTRY, client)
        assert not errors, f"Simple query plan invalid: {errors}"
        assert len(plan.steps) >= 1

    def test_medium_query_valid_plan(self, client):
        plan, errors = generate_plan(MEDIUM_QUERY, REGISTRY, client)
        assert not errors, f"Medium query plan invalid: {errors}"

    def test_complex_query_more_steps_than_simple(self, client):
        simple_plan, _ = generate_plan(SIMPLE_QUERY, REGISTRY, client)
        complex_plan, _ = generate_plan(COMPLEX_QUERY, REGISTRY, client)
        assert len(complex_plan.steps) >= len(simple_plan.steps), (
            f"Complex plan ({len(complex_plan.steps)} steps) not >= "
            f"simple plan ({len(simple_plan.steps)} steps)"
        )

    def test_all_executors_in_registry(self, client):
        plan, _ = generate_plan(MEDIUM_QUERY, REGISTRY, client)
        for step in plan.steps:
            assert step.executor in REGISTRY, (
                f"Executor '{step.executor}' not in registry"
            )

    def test_no_unknown_depends_on(self, client):
        plan, _ = generate_plan(COMPLEX_QUERY, REGISTRY, client)
        ids = {s.id for s in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                assert dep in ids, (
                    f"Step '{step.id}' depends_on unknown '{dep}'"
                )

    def test_budget_within_limit(self, client):
        plan, _ = generate_plan(COMPLEX_QUERY, REGISTRY, client)
        assert plan.budget_tokens <= MAX_BUDGET_TOKENS

    def test_forced_cycle_caught_by_validator(self):
        """Force-feed a cyclic plan to validator — must catch before any execution."""
        from agents.planner import Plan, Step
        bad = Plan(
            steps=[
                Step(id="s1", intent="x", executor="get_historical_ohlcv",
                     depends_on=["s2"], expected_output="y"),
                Step(id="s2", intent="x", executor="calculate_indicators",
                     depends_on=["s1"], expected_output="y"),
            ],
            budget_tokens=5000,
        )
        errs = validate_plan(bad, REGISTRY)
        assert any("Circular" in e for e in errs), (
            f"Cycle not caught: {errs}"
        )

    def test_fallback_plan_used_when_registry_empty(self, client):
        """Empty registry → every executor invalid → fallback to default_plan."""
        plan, errors = generate_plan(SIMPLE_QUERY, set(), client)
        # Fallback default_plan also fails empty-registry validation,
        # but generate_plan still returns it (not None) — caller decides.
        assert plan is not None
        assert len(plan.steps) >= 1

    def test_default_plan_passes_real_registry(self):
        plan = default_plan("Phân tích HPG")
        errs = validate_plan(plan, REGISTRY)
        assert errs == [], f"Default plan invalid against real registry: {errs}"
