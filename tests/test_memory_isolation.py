"""
tests/test_memory_isolation.py — Bài 30: 5 memory isolation tests.

Tests the data-layer isolation guarantees — no LLM calls needed.
Verifies that memory and conversation data cannot bleed across users, tenants, or conversations.

Test cases:
  1. user_memory_cross_user       — user_A memory invisible to user_B
  2. conversation_history_cross   — conv_A messages invisible to load_history(conv_B)
  3. similar_prefs_no_bleed       — same key for two users stays separate
  4. cross_tenant_isolation        — same user_id, different tenant → no bleed
  5. load_history_own_conv_only   — same user, two convs → load_history returns only own conv
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from memory.conversation import create_conversation, load_history, save_turn
from memory.reader import load_user_memory, save_memory_item


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid(label: str = "") -> str:
    return f"iso_test_{label}_{uuid.uuid4().hex[:8]}"


def _cid() -> str:
    return str(uuid.uuid4())


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_user_memory_cross_user_isolation():
    """User A's memory item must NOT appear when user B loads their memory."""
    user_a = _uid("a")
    user_b = _uid("b")
    tenant = "default"

    save_memory_item(
        user_id=user_a,
        tenant_id=tenant,
        key="sector_preference",
        value="thep",
        confidence=0.9,
        source_message="Tôi thích ngành thép",
    )

    user_b_items = load_user_memory(user_b, tenant)
    keys = [item["key"] for item in user_b_items]
    assert "sector_preference" not in keys or all(
        item["key"] != "sector_preference" or item.get("value") != "thep"
        for item in user_b_items
    ), "user_B must not see user_A's sector_preference=thep"


def test_conversation_history_cross_conversation_isolation():
    """Messages saved to conv_A must not appear in load_history(conv_B), even same user."""
    user = _uid("hist")
    conv_a = create_conversation(user)
    conv_b = create_conversation(user)

    save_turn(conv_a, "Phân tích HPG cho tôi", "HPG là cổ phiếu thép lớn nhất Việt Nam.")

    history_b = load_history(conv_b, limit=10)
    contents = [m["content"] for m in history_b]
    assert not any("HPG" in c for c in contents), (
        "load_history(conv_B) must not return messages from conv_A"
    )


def test_similar_prefs_two_users_no_bleed():
    """Two users with the same preference key stay independent — no cross-contamination."""
    user_x = _uid("x")
    user_y = _uid("y")
    tenant = "default"

    save_memory_item(user_x, tenant, "risk_tolerance", "cao", 0.85, "Tôi thích rủi ro cao")
    save_memory_item(user_y, tenant, "risk_tolerance", "thap", 0.85, "Tôi ngại rủi ro")

    items_x = load_user_memory(user_x, tenant)
    items_y = load_user_memory(user_y, tenant)

    val_x = next((i["value"] for i in items_x if i["key"] == "risk_tolerance"), None)
    val_y = next((i["value"] for i in items_y if i["key"] == "risk_tolerance"), None)

    assert val_x is not None, "user_X must have risk_tolerance"
    assert val_y is not None, "user_Y must have risk_tolerance"

    # Deserialize JSON-stored values for comparison
    import json
    def _v(v: str) -> str:
        try:
            return json.loads(v)
        except Exception:
            return v

    assert _v(val_x) == "cao", f"user_X should have cao, got {val_x!r}"
    assert _v(val_y) == "thap", f"user_Y should have thap, got {val_y!r}"


def test_cross_tenant_isolation():
    """Same user_id but different tenant_id must NOT share memory items."""
    user = _uid("tenant")
    tenant_a = "tenant_alpha"
    tenant_b = "tenant_beta"

    save_memory_item(user, tenant_a, "preferred_exchange", "HOSE", 0.9, "HOSE là sàn tôi dùng")

    # tenant_b should NOT see tenant_a's item
    items_b = load_user_memory(user, tenant_b)
    keys_b = [i["key"] for i in items_b]
    assert "preferred_exchange" not in keys_b, (
        f"tenant_beta must not see tenant_alpha's memory; got keys={keys_b}"
    )


def test_load_history_returns_only_own_conversation():
    """load_history(conv_id) must return only messages for that exact conversation."""
    user = _uid("conv")
    conv1 = create_conversation(user)
    conv2 = create_conversation(user)

    save_turn(conv1, "Câu hỏi A trong hội thoại 1", "Trả lời A")
    save_turn(conv2, "Câu hỏi B trong hội thoại 2", "Trả lời B")

    hist1 = load_history(conv1, limit=20)
    hist2 = load_history(conv2, limit=20)

    contents1 = [m["content"] for m in hist1]
    contents2 = [m["content"] for m in hist2]

    # Conv1 must contain its own messages
    assert any("hội thoại 1" in c for c in contents1), "conv1 history missing its own messages"
    # Conv1 must NOT contain conv2 messages
    assert not any("hội thoại 2" in c for c in contents1), (
        "conv1 history leaked conv2 messages"
    )
    # Conv2 must contain its own messages
    assert any("hội thoại 2" in c for c in contents2), "conv2 history missing its own messages"
    # Conv2 must NOT contain conv1 messages
    assert not any("hội thoại 1" in c for c in contents2), (
        "conv2 history leaked conv1 messages"
    )
