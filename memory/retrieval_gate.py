"""
memory/retrieval_gate.py — Small-model gate deciding whether memory retrieval is needed.

should_retrieve(query) -> (should_retrieve: bool, refined_query: str)

Disabled by default (RETRIEVAL_GATE env flag). When disabled, returns (True, query)
so existing memory-read behavior is unchanged. When enabled, a small model call decides.
Fails open (returns True, query) on any error.
"""
from __future__ import annotations

import json
import os

_SYSTEM = """Decide if retrieving conversation history helps answer this query.
Reply ONLY with JSON: {"retrieve": true/false, "query": "refined search query if retrieve=true", "reason": "one line"}
retrieve=false for: greetings, simple real-time price checks, market breadth queries.
retrieve=true for: follow-up questions, personalization needed, context-dependent queries."""


def _enabled() -> bool:
    return os.getenv("RETRIEVAL_GATE", "0").strip().lower() in ("1", "true", "yes", "on")


def should_retrieve(query: str) -> tuple[bool, str]:
    """Returns (should_retrieve, refined_query). Fails open. No-op when flag off."""
    if not _enabled():
        return True, query
    try:
        from llm.factory import create_client
        from llm.types import Message
        client = create_client()
        resp = client.generate(
            [Message(role="user", content=f"Query: {query}")],
            system=_SYSTEM,
            max_tokens=128,
            temperature=0.0,
        )
        data = json.loads(resp.text.strip())
        return bool(data.get("retrieve", True)), data.get("query", query)
    except Exception:
        return True, query  # fail open
