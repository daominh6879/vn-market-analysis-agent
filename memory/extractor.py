"""
memory/extractor.py — LLM-based preference extraction (Bài 28).

extract_preferences(turn_messages) → list[MemoryItem]

Only items with confidence >= 0.7 are returned — caller decides whether to save.
Run AFTER the turn completes, not during — to avoid saving hypothetical statements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from llm.factory import create_client
from llm.types import Message

CONFIDENCE_THRESHOLD = 0.7

_SYSTEM = """You are a preference extractor. Given a conversation exchange, identify concrete user preferences, interests, or constraints that should be remembered for future conversations.

Rules:
- Only extract clear, explicit preferences (NOT hypotheticals, NOT questions).
- Assign confidence 0.0–1.0. Assign < 0.7 for vague/uncertain statements ("chắc là", "có thể", "nếu như").
- Output ONLY a JSON array. Each item: {"key": str, "value": str, "confidence": float, "source_message": str}
- key: short snake_case label (e.g. "preferred_sector", "risk_tolerance", "preferred_ticker")
- value: extracted preference as a string
- source_message: the exact user sentence that expressed this preference
- If no preferences found, return []

Examples of LOW confidence (< 0.7):
- "chắc là tôi hơi thích ngành thép" → confidence 0.4
- "nếu tôi ưa rủi ro cao thì sao?" → confidence 0.0 (hypothetical)
- "có thể tôi quan tâm đến FPT" → confidence 0.5

Examples of HIGH confidence (>= 0.7):
- "tôi thích đầu tư vào ngành công nghệ" → confidence 0.85
- "tôi không quan tâm đến ngành thép" → confidence 0.9
- "hãy phân tích với góc nhìn dài hạn" → confidence 0.8
"""


@dataclass
class MemoryItem:
    key: str
    value: Any
    confidence: float
    source_message: str = ""

    @property
    def should_save(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


def extract_preferences(turn_messages: list[dict]) -> list[MemoryItem]:
    """
    Call LLM to extract preferences from a list of {role, content} messages.
    Returns only items with confidence >= CONFIDENCE_THRESHOLD.
    """
    if not turn_messages:
        return []

    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in turn_messages
    )

    client = create_client()
    response = client.generate(
        messages=[Message(role="user", content=f"Conversation:\n{conversation_text}")],
        system=_SYSTEM,
        max_tokens=512,
        temperature=0.0,
    )

    raw = response.text.strip()

    # Extract JSON array from response (handle markdown fences)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            mi = MemoryItem(
                key=str(item.get("key", "")).strip(),
                value=str(item.get("value", "")).strip(),
                confidence=float(item.get("confidence", 0.0)),
                source_message=str(item.get("source_message", "")).strip(),
            )
            if mi.key and mi.should_save:
                result.append(mi)
        except (TypeError, ValueError):
            continue

    return result
