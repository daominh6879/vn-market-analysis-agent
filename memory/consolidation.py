"""
memory/consolidation.py — Extract facts + episode from recent turns via small model.

consolidate(turns) -> ConsolidationResult | None
maybe_consolidate(conversation_id, user_id, turn_count, turns, first_question, tenant_id)

Runs every CONSOLIDATE_EVERY turns (default 6). Persists facts to user_memory
(semantic) and the episode summary to episodic memory (Qdrant).

Note: adapted from tini-agent — facts go to user_memory via save_memory_item
(keyed by content hash so distinct facts coexist), episodes via store_episode.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

CONSOLIDATE_EVERY = int(os.getenv("CONSOLIDATE_EVERY", "6"))

_SYSTEM = """Read this conversation excerpt and extract:
1. facts[]: durable user preferences or facts worth remembering (e.g. "user tracks HPG and VCB")
2. episode: one-sentence summary of what was discussed today

Reply ONLY with JSON: {"facts": ["...", ...], "episode": "..."}
If nothing worth remembering, return {"facts": [], "episode": ""}"""


@dataclass
class ConsolidationResult:
    facts: list[str]
    episode: str


def consolidate(turns: list[dict]) -> ConsolidationResult | None:
    """Extract facts + episode from a list of {role, content} turns."""
    if not turns:
        return None
    try:
        from llm.factory import create_client
        from llm.types import Message
        client = create_client()
        excerpt = "\n".join(
            f"{t['role'].upper()}: {t['content'][:200]}"
            for t in turns[-12:]  # last 12 messages
        )
        resp = client.generate(
            [Message(role="user", content=excerpt)],
            system=_SYSTEM,
            max_tokens=512,
            temperature=0.0,
        )
        data = json.loads(resp.text.strip())
        return ConsolidationResult(
            facts=data.get("facts", []),
            episode=data.get("episode", ""),
        )
    except Exception:
        return None


def maybe_consolidate(
    conversation_id: str,
    user_id: str,
    turn_count: int,
    turns: list[dict],
    first_question: str = "",
    tenant_id: str = "default",
) -> None:
    """Call after each turn. Runs consolidation every CONSOLIDATE_EVERY turns."""
    if turn_count % CONSOLIDATE_EVERY != 0:
        return
    result = consolidate(turns)
    if not result:
        return

    # Persist facts to semantic memory (user_memory)
    try:
        from memory.reader import save_memory_item
        for fact in result.facts:
            if not fact.strip():
                continue
            key = "consolidated_fact:" + hashlib.sha1(fact.encode("utf-8")).hexdigest()[:12]
            save_memory_item(
                user_id=user_id,
                tenant_id=tenant_id,
                key=key,
                value=fact,
                confidence=1.0,
                source_message=fact,
            )
    except Exception:
        pass

    # Persist episode summary to episodic memory (Qdrant)
    try:
        from memory.episodic import store_episode
        if result.episode:
            store_episode(
                conversation_id=conversation_id,
                user_id=user_id,
                first_question=first_question,
                summary=result.episode,
                conclusion=result.episode,
            )
    except Exception:
        pass
