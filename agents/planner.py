"""
agents/planner.py — Structured planning for bài 23.

Plan = data with schema, not prose.
Model generates JSON → code validates → execute or retry → fallback.
"""

from __future__ import annotations

from pydantic import BaseModel

MAX_STEPS = 10
MAX_BUDGET_TOKENS = 20_000


class Step(BaseModel):
    id: str
    intent: str
    executor: str        # tool name or agent name
    depends_on: list[str]
    expected_output: str


class Plan(BaseModel):
    steps: list[Step]
    budget_tokens: int


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_plan(plan: Plan, registry: set[str]) -> list[str]:
    """Return list of validation errors. Empty list = valid.

    Checks 5 conditions:
    1. No circular dependencies
    2. All depends_on reference valid ids
    3. All executors exist in registry
    4. Total steps <= MAX_STEPS
    5. budget_tokens <= MAX_BUDGET_TOKENS
    """
    errors: list[str] = []
    ids = {step.id for step in plan.steps}

    if len(plan.steps) > MAX_STEPS:
        errors.append(f"Too many steps: {len(plan.steps)} > {MAX_STEPS}")

    if plan.budget_tokens > MAX_BUDGET_TOKENS:
        errors.append(
            f"budget_tokens {plan.budget_tokens} exceeds hard limit {MAX_BUDGET_TOKENS}"
        )

    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in ids:
                errors.append(
                    f"Step '{step.id}' depends_on unknown id '{dep}'"
                )
        if step.executor not in registry:
            errors.append(
                f"Step '{step.id}' executor '{step.executor}' not in registry"
            )

    errors.extend(_detect_cycles(plan.steps))
    return errors


def _detect_cycles(steps: list[Step]) -> list[str]:
    """DFS cycle detection on dependency graph. Returns error strings."""
    graph: dict[str, list[str]] = {s.id: s.depends_on for s in steps}
    # 0=white 1=gray 2=black
    color: dict[str, int] = {s.id: 0 for s in steps}
    errors: list[str] = []

    def dfs(node: str) -> bool:
        color[node] = 1
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == 1:
                errors.append(
                    f"Circular dependency: '{node}' -> '{dep}'"
                )
                return True
            if color[dep] == 0:
                if dfs(dep):
                    return True
        color[node] = 2
        return False

    for step_id in list(graph.keys()):
        if color.get(step_id, 2) == 0:
            dfs(step_id)

    return errors


# ── Default fallback plan (bài 22 pattern) ────────────────────────────────────

def default_plan(query: str) -> Plan:  # noqa: ARG001
    """Sequential fallback used when model planning fails twice."""
    return Plan(
        steps=[
            Step(
                id="s1",
                intent="Fetch OHLCV price history",
                executor="get_historical_ohlcv",
                depends_on=[],
                expected_output="CSV path with price history",
            ),
            Step(
                id="s2",
                intent="Calculate technical indicators from price data",
                executor="calculate_indicators",
                depends_on=["s1"],
                expected_output="Technical signals string",
            ),
            Step(
                id="s3",
                intent="Search financial news",
                executor="search_financial_news",
                depends_on=[],
                expected_output="News summary",
            ),
            Step(
                id="s4",
                intent="Analyze sentiment and synthesize report",
                executor="analyze_market_sentiment",
                depends_on=["s2", "s3"],
                expected_output="Final markdown report",
            ),
        ],
        budget_tokens=8_000,
    )


# ── LLM-based plan generation with retry ─────────────────────────────────────

def generate_plan(
    query: str,
    registry: set[str],
    client,
) -> tuple[Plan, list[str]]:
    """Generate plan via LLM. Retry once with errors. Fall back to default.

    Returns (plan, validation_errors_of_final_attempt).
    Empty errors = valid plan from model. Non-empty = used fallback.
    """
    from llm.types import Message

    registry_list = sorted(registry)
    schema_hint = (
        '{"steps": [{"id": "s1", "intent": "...", "executor": "<tool>", '
        '"depends_on": [], "expected_output": "..."}], "budget_tokens": 8000}'
    )

    def build_prompt(prior_errors: str = "") -> str:
        base = (
            f"Query: {query}\n\n"
            f"Available executors: {registry_list}\n\n"
            f"Output ONLY JSON, no markdown fences. Schema: {schema_hint}\n\n"
            f"Rules: depends_on uses step ids only. "
            f"budget_tokens <= {MAX_BUDGET_TOKENS}. max {MAX_STEPS} steps. "
            "No circular dependencies."
        )
        if prior_errors:
            base += f"\n\nPrevious attempt failed with:\n{prior_errors}\nFix and retry."
        return base + "\n\nPlan JSON:"

    system = (
        "You are a financial analysis planner. "
        "Output ONLY valid JSON. No prose, no markdown fences, no explanation."
    )

    def attempt(prompt: str) -> tuple[Plan | None, list[str]]:
        resp = client.generate(
            [Message(role="user", content=prompt)],
            max_tokens=2000,
            system=system,
        )
        raw = resp.text.strip()
        # Strip markdown fences if model ignores instructions
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and "budget_tokens" not in parsed:
                parsed["budget_tokens"] = 8000
            plan = Plan.model_validate(parsed)
            errs = validate_plan(plan, registry)
            return plan, errs
        except Exception as exc:
            return None, [f"JSON parse error: {exc}"]

    plan, errors = attempt(build_prompt())
    if plan and not errors:
        return plan, []

    plan2, errors2 = attempt(build_prompt(prior_errors="\n".join(errors)))
    if plan2 and not errors2:
        return plan2, []

    return default_plan(query), errors2 or errors
