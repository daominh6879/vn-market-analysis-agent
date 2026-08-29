"""
memory/procedural.py — Procedural memory: rules derived from explicit user feedback (Bài 29).

Rules are ONLY generated when the user sends an explicit signal (a direct feedback message),
never from model inference alone.

generate_rules(feedback_message) → list[ProceduralRule]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


from llm.factory import create_client
from llm.types import Message

_SYSTEM = """You are a rule extractor. Given explicit user feedback about how they want the assistant to behave, extract concrete procedural rules.

Rules:
- Only extract rules from EXPLICIT user instructions or corrections ("don't do X", "always Y", "I prefer Z format").
- Do NOT infer rules from normal conversation — only from direct feedback.
- Output ONLY a JSON array. Each item: {"rule": str, "trigger": str, "priority": int}
  - rule: what the assistant should do (imperative sentence)
  - trigger: condition when this rule applies (e.g. "when showing financial analysis")
  - priority: 1–5 (5 = highest, overrides defaults)
- If no rules found, return []
"""


@dataclass
class ProceduralRule:
    rule: str
    trigger: str
    priority: int


def generate_rules(feedback_message: str) -> list[ProceduralRule]:
    """
    Extract procedural rules from an explicit user feedback message.
    Only call this when the user has actively provided feedback/correction.
    """
    if not feedback_message.strip():
        return []

    client = create_client()
    response = client.generate(
        messages=[Message(role="user", content=f"User feedback:\n{feedback_message}")],
        system=_SYSTEM,
        max_tokens=512,
        temperature=0.0,
    )

    raw = response.text.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    rules = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rule_text = str(item.get("rule", "")).strip()
        trigger = str(item.get("trigger", "always")).strip()
        try:
            priority = int(item.get("priority", 3))
        except (TypeError, ValueError):
            priority = 3
        if rule_text:
            rules.append(ProceduralRule(rule=rule_text, trigger=trigger, priority=priority))

    return rules
