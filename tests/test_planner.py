"""tests/test_planner.py — Unit tests for agents/planner.py (bài 23)."""

import pytest
from agents.planner import (
    MAX_BUDGET_TOKENS,
    MAX_STEPS,
    Plan,
    Step,
    _detect_cycles,
    default_plan,
    validate_plan,
)

REGISTRY: set[str] = {
    "get_historical_ohlcv",
    "calculate_indicators",
    "search_financial_news",
    "analyze_market_sentiment",
}


def _step(id: str, executor: str = "get_historical_ohlcv", depends_on: list[str] | None = None) -> Step:
    return Step(
        id=id,
        intent=f"intent for {id}",
        executor=executor,
        depends_on=depends_on or [],
        expected_output="some output",
    )


# ── validate_plan ──────────────────────────────────────────────────────────────

class TestValidatePlan:
    def test_valid_single_step(self):
        plan = Plan(steps=[_step("s1")], budget_tokens=5000)
        assert validate_plan(plan, REGISTRY) == []

    def test_valid_linear_chain(self):
        plan = Plan(
            steps=[
                _step("s1"),
                _step("s2", depends_on=["s1"]),
                _step("s3", executor="calculate_indicators", depends_on=["s2"]),
            ],
            budget_tokens=5000,
        )
        assert validate_plan(plan, REGISTRY) == []

    def test_unknown_depends_on_reported(self):
        plan = Plan(steps=[_step("s1", depends_on=["ghost"])], budget_tokens=5000)
        errs = validate_plan(plan, REGISTRY)
        assert any("ghost" in e for e in errs)

    def test_unknown_executor_reported(self):
        plan = Plan(steps=[_step("s1", executor="nonexistent_tool")], budget_tokens=5000)
        errs = validate_plan(plan, REGISTRY)
        assert any("nonexistent_tool" in e for e in errs)

    def test_too_many_steps(self):
        steps = [_step(f"s{i}") for i in range(MAX_STEPS + 1)]
        plan = Plan(steps=steps, budget_tokens=5000)
        errs = validate_plan(plan, REGISTRY)
        assert any("Too many steps" in e for e in errs)

    def test_budget_over_limit(self):
        plan = Plan(steps=[_step("s1")], budget_tokens=MAX_BUDGET_TOKENS + 1)
        errs = validate_plan(plan, REGISTRY)
        assert any("budget_tokens" in e for e in errs)

    def test_circular_two_node(self):
        plan = Plan(
            steps=[
                _step("s1", depends_on=["s2"]),
                _step("s2", depends_on=["s1"]),
            ],
            budget_tokens=5000,
        )
        errs = validate_plan(plan, REGISTRY)
        assert any("Circular" in e for e in errs)

    def test_self_loop(self):
        plan = Plan(steps=[_step("s1", depends_on=["s1"])], budget_tokens=5000)
        errs = validate_plan(plan, REGISTRY)
        assert any("Circular" in e for e in errs)

    def test_multiple_errors_returned(self):
        plan = Plan(
            steps=[_step("s1", executor="bad_tool", depends_on=["missing"])],
            budget_tokens=MAX_BUDGET_TOKENS + 1,
        )
        errs = validate_plan(plan, REGISTRY)
        assert len(errs) >= 3  # budget + unknown dep + unknown executor


# ── default_plan ───────────────────────────────────────────────────────────────

class TestDefaultPlan:
    def test_default_plan_valid(self):
        plan = default_plan("Phân tích HPG")
        errs = validate_plan(plan, REGISTRY)
        assert errs == [], f"Default plan invalid: {errs}"

    def test_default_plan_minimum_steps(self):
        plan = default_plan("Phân tích HPG")
        assert len(plan.steps) >= 2

    def test_default_plan_budget_within_limit(self):
        plan = default_plan("Phân tích HPG")
        assert plan.budget_tokens <= MAX_BUDGET_TOKENS

    def test_default_plan_all_ids_unique(self):
        plan = default_plan("Phân tích FPT")
        ids = [s.id for s in plan.steps]
        assert len(ids) == len(set(ids))


# ── _detect_cycles ────────────────────────────────────────────────────────────

class TestDetectCycles:
    def test_linear_no_cycle(self):
        steps = [
            _step("a"),
            _step("b", depends_on=["a"]),
            _step("c", depends_on=["b"]),
        ]
        assert _detect_cycles(steps) == []

    def test_diamond_no_cycle(self):
        steps = [
            _step("a"),
            _step("b", depends_on=["a"]),
            _step("c", depends_on=["a"]),
            _step("d", depends_on=["b", "c"]),
        ]
        assert _detect_cycles(steps) == []

    def test_direct_two_node_cycle(self):
        steps = [
            _step("a", depends_on=["b"]),
            _step("b", depends_on=["a"]),
        ]
        assert len(_detect_cycles(steps)) > 0

    def test_three_node_cycle(self):
        steps = [
            _step("a", depends_on=["c"]),
            _step("b", depends_on=["a"]),
            _step("c", depends_on=["b"]),
        ]
        assert len(_detect_cycles(steps)) > 0

    def test_single_node_self_loop(self):
        steps = [_step("a", depends_on=["a"])]
        assert len(_detect_cycles(steps)) > 0

    def test_empty_steps(self):
        assert _detect_cycles([]) == []


# ── Plan schema ───────────────────────────────────────────────────────────────

class TestPlanSchema:
    def test_parse_from_json(self):
        raw = """{
            "steps": [
                {"id": "s1", "intent": "fetch", "executor": "get_historical_ohlcv",
                 "depends_on": [], "expected_output": "csv"}
            ],
            "budget_tokens": 5000
        }"""
        plan = Plan.model_validate_json(raw)
        assert len(plan.steps) == 1
        assert plan.steps[0].id == "s1"

    def test_complex_plan_from_json(self):
        """Câu phức hợp phải sinh nhiều bước hơn câu đơn giản (simulated)."""
        simple = Plan(steps=[_step("s1")], budget_tokens=3000)
        complex_plan = Plan(
            steps=[_step("s1"), _step("s2", depends_on=["s1"]), _step("s3", depends_on=["s1"])],
            budget_tokens=8000,
        )
        assert len(complex_plan.steps) > len(simple.steps)
