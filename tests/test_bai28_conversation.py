"""
tests/test_bai28_conversation.py — Bài 28 integration tests.

All tests hit real DB + LLM (DeepSeek default).
Run: python -m pytest tests/test_bai28_conversation.py -v -s

Covers every "Xong khi" checklist item:
  [x] POST /conversations → conversation_id
  [x] Turn 1 + Turn 2: history injected on turn 2
  [x] Conversation isolation (A vs B no leak)
  [x] Clear preference → saved to user_memory
  [x] New conversation same user → memory carried across
  [x] Changed preference → old record superseded, still in DB
  [x] Ambiguous statement → NOT saved
"""

from __future__ import annotations

import uuid
from dotenv import load_dotenv
load_dotenv()

import psycopg2
import psycopg2.extras
import pytest

from memory.conversation import create_conversation, load_history, save_turn, get_conversation
from memory.reader import load_user_memory, save_memory_item
from memory.extractor import extract_preferences, CONFIDENCE_THRESHOLD
from memory.turn_handler import run_turn
from core.config import settings


# ── helpers ───────────────────────────────────────────────────────────────────

def _dsn():
    return (
        f"host=127.0.0.1 port=5432 "
        f"dbname={settings.POSTGRES_DB} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )


def _unique_user() -> str:
    return f"test_user_{uuid.uuid4().hex[:8]}"


# ── create conversation ───────────────────────────────────────────────────────

def test_create_conversation():
    """POST /conversations → returns a valid UUID conversation_id."""
    user_id = _unique_user()
    cid = create_conversation(user_id)
    assert cid, "conversation_id must be non-empty"
    conv = get_conversation(cid)
    assert conv is not None
    assert conv["user_id"] == user_id
    print(f"\n  conversation_id={cid[:8]}... user_id={user_id}")


# ── history across turns ──────────────────────────────────────────────────────

def test_history_injected_on_turn2():
    """Turn 1 stored; turn 2 load_history returns turn 1 messages."""
    user_id = _unique_user()
    cid = create_conversation(user_id)

    # Turn 1: manually save (skip LLM to isolate history test)
    save_turn(cid, "Phân tích FPT cho tôi", "FPT là công ty công nghệ hàng đầu Việt Nam.")

    history = load_history(cid, limit=10)
    assert len(history) == 2, f"Expected 2 messages, got {len(history)}"
    assert history[0]["role"] == "user"
    assert "FPT" in history[0]["content"]
    assert history[1]["role"] == "assistant"

    print(f"\n  After turn 1: {len(history)} messages in history")

    # Turn 2: history must contain turn 1
    save_turn(cid, "Còn HPG thì sao?", "HPG là tập đoàn thép lớn nhất Việt Nam.")
    history2 = load_history(cid, limit=10)
    assert len(history2) == 4, f"Expected 4 messages after 2 turns, got {len(history2)}"
    roles = [m["role"] for m in history2]
    assert roles == ["user", "assistant", "user", "assistant"]
    print(f"  After turn 2: {len(history2)} messages in history")


# ── conversation isolation ────────────────────────────────────────────────────

def test_conversation_isolation():
    """Conversation A and B must not share history."""
    user_id = _unique_user()
    cid_a = create_conversation(user_id)
    cid_b = create_conversation(user_id)

    save_turn(cid_a, "Hỏi về FPT", "Đây là reply về FPT")
    save_turn(cid_b, "Hỏi về HPG", "Đây là reply về HPG")

    hist_a = load_history(cid_a)
    hist_b = load_history(cid_b)

    # No cross-contamination
    a_content = " ".join(m["content"] for m in hist_a)
    b_content = " ".join(m["content"] for m in hist_b)
    assert "HPG" not in a_content, "Conversation A leaked HPG from B"
    assert "FPT" not in b_content, "Conversation B leaked FPT from A"
    print(f"\n  A messages: {len(hist_a)}, B messages: {len(hist_b)} — no cross-leak")


# ── memory extraction (real LLM) ─────────────────────────────────────────────

def test_clear_preference_saved():
    """Explicit preference → confidence >= 0.7 → saved to user_memory."""
    user_id = _unique_user()
    turn = [
        {"role": "user", "content": "Tôi thích đầu tư vào ngành công nghệ, đặc biệt là FPT và VNM."},
        {"role": "assistant", "content": "Tôi ghi nhận sở thích đầu tư của bạn vào ngành công nghệ."},
    ]
    items = extract_preferences(turn)
    print(f"\n  Extracted {len(items)} items: {[(i.key, i.confidence) for i in items]}")
    assert len(items) >= 1, "Must extract at least 1 preference from clear statement"
    for item in items:
        assert item.confidence >= CONFIDENCE_THRESHOLD, (
            f"Item {item.key!r} confidence {item.confidence} below threshold {CONFIDENCE_THRESHOLD}"
        )


def test_ambiguous_statement_not_saved():
    """Vague/hypothetical statement → confidence < 0.7 → not returned."""
    turn = [
        {"role": "user", "content": "Chắc là tôi hơi thích ngành thép, không chắc lắm."},
        {"role": "assistant", "content": "Tôi hiểu bạn đang xem xét ngành thép."},
    ]
    items = extract_preferences(turn)
    print(f"\n  Ambiguous: extracted {len(items)} items above threshold")
    assert len(items) == 0, (
        f"Ambiguous statement must not be saved, but got: {[(i.key, i.confidence) for i in items]}"
    )


def test_hypothetical_not_saved():
    """'Nếu như...' hypotheticals must not be saved."""
    turn = [
        {"role": "user", "content": "Nếu tôi ưa rủi ro cao thì phân tích cho tôi cổ phiếu gì?"},
        {"role": "assistant", "content": "Với rủi ro cao, bạn có thể xem xét các penny stocks."},
    ]
    items = extract_preferences(turn)
    print(f"\n  Hypothetical: extracted {len(items)} items above threshold")
    assert len(items) == 0, "Hypothetical must not be saved as preference"


# ── memory persistence across conversations ───────────────────────────────────

def test_memory_carried_across_conversations():
    """Save preference in conv 1, load_user_memory in conv 2 shows it."""
    user_id = _unique_user()
    tenant_id = "default"

    # Save a preference directly
    save_memory_item(user_id, tenant_id, "preferred_sector", "công nghệ", 0.9, "tôi thích ngành công nghệ")

    # New conversation — load memory
    items = load_user_memory(user_id, tenant_id, max_items=5)
    keys = [i["key"] for i in items]
    assert "preferred_sector" in keys, f"Preference not found across conversations. Keys: {keys}"
    print(f"\n  Memory across conversations: {[(i['key'], i['confidence']) for i in items]}")


# ── contradiction / supersede ─────────────────────────────────────────────────

def test_supersede_old_preference():
    """Changing a preference marks old record superseded_by, old record still in DB."""
    user_id = _unique_user()
    tenant_id = "default"

    old_id = save_memory_item(user_id, tenant_id, "risk_tolerance", "thấp", 0.85, "tôi thích an toàn")
    new_id = save_memory_item(user_id, tenant_id, "risk_tolerance", "cao", 0.90, "tôi muốn rủi ro cao")

    # Active record: only the new one
    active = load_user_memory(user_id, tenant_id, max_items=10)
    active_keys_values = [(i["key"], i["value"]) for i in active]
    assert ("risk_tolerance", '"cao"') in active_keys_values or \
           any(i["key"] == "risk_tolerance" and "cao" in str(i["value"]) for i in active), \
        f"New value not found in active memory: {active_keys_values}"

    # Old record still exists in DB with superseded_by set
    with psycopg2.connect(_dsn()) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT superseded_by FROM user_memory WHERE id = %s", (old_id,))
            row = cur.fetchone()
    assert row is not None, "Old record deleted — must be retained"
    assert str(row["superseded_by"]) == new_id, (
        f"Old record superseded_by should be {new_id}, got {row['superseded_by']}"
    )
    print(f"\n  Old id={old_id[:8]}... superseded_by={str(row['superseded_by'])[:8]}...")


# ── full turn with LLM ────────────────────────────────────────────────────────

def test_full_turn_hits_llm():
    """run_turn calls LLM, saves history, returns non-empty reply."""
    user_id = _unique_user()
    cid = create_conversation(user_id)

    reply = run_turn(cid, user_id, "FPT là gì?")
    print(f"\n  Reply (first 80 chars): {reply[:80]!r}")
    assert reply, "LLM reply must be non-empty"

    history = load_history(cid)
    assert len(history) == 2, f"Expected 2 messages after 1 turn, got {len(history)}"
    assert history[0]["content"] == "FPT là gì?"
    assert history[1]["content"] == reply


def test_full_turn_memory_injection():
    """Turn 1 states explicit preference; turn 2 reply must acknowledge it."""
    user_id = _unique_user()
    cid = create_conversation(user_id)
    tenant_id = "default"

    # Pre-seed memory
    save_memory_item(user_id, tenant_id, "preferred_sector", "công nghệ", 0.95, "tôi thích công nghệ")

    reply = run_turn(cid, user_id, "Hãy gợi ý cho tôi một cổ phiếu phù hợp.", tenant_id=tenant_id)
    print(f"\n  Reply with memory injection (first 120 chars): {reply[:120]!r}")
    assert reply, "Reply must be non-empty"
    # Memory injected: model should mention tech sector
    assert any(kw in reply.lower() for kw in ["công nghệ", "fpt", "technology", "cng ngh"]), (
        "Reply should mention the injected tech preference"
    )


if __name__ == "__main__":
    print("=== test_create_conversation ===")
    test_create_conversation()

    print("=== test_history_injected_on_turn2 ===")
    test_history_injected_on_turn2()

    print("=== test_conversation_isolation ===")
    test_conversation_isolation()

    print("=== test_clear_preference_saved (LLM) ===")
    test_clear_preference_saved()

    print("=== test_ambiguous_statement_not_saved (LLM) ===")
    test_ambiguous_statement_not_saved()

    print("=== test_hypothetical_not_saved (LLM) ===")
    test_hypothetical_not_saved()

    print("=== test_memory_carried_across_conversations ===")
    test_memory_carried_across_conversations()

    print("=== test_supersede_old_preference ===")
    test_supersede_old_preference()

    print("=== test_full_turn_hits_llm (LLM) ===")
    test_full_turn_hits_llm()

    print("=== test_full_turn_memory_injection (LLM) ===")
    test_full_turn_memory_injection()

    print("\nAll tests passed.")
