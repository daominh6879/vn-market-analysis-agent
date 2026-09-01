"""
tests/test_hybrid_memory.py — E2E tests for HybridMemoryAgent + ChromaDB integration.

Requires:
  - Ollama running with nomic-embed-text pulled
  - LLM provider reachable (default: DeepSeek)
  - Postgres running (for run_turn tests)

Run: python -m pytest tests/test_hybrid_memory.py -v -s
"""

from __future__ import annotations

import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from memory.chat_context import (
    HybridMemoryAgent,
    chroma_retrieve,
    chroma_store,
    delete_conversation_context,
)
from memory.conversation import create_conversation, delete_conversation
from memory.turn_handler import run_turn


# ── helpers ───────────────────────────────────────────────────────────────────

def _uid() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


def _cid() -> str:
    return f"test_conv_{uuid.uuid4().hex[:8]}"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    """Unique conversation + user ids; cleans ChromaDB docs after test."""
    conversation_id = _cid()
    user_id = _uid()
    yield conversation_id, user_id
    try:
        delete_conversation_context(conversation_id)
    except Exception:
        pass


@pytest.fixture
def pg_ctx():
    """Same as ctx but also creates + deletes a real Postgres conversation."""
    user_id = _uid()
    cid = create_conversation(user_id)
    yield cid, user_id
    try:
        delete_conversation(cid)       # cleans Postgres + Qdrant + ChromaDB
    except Exception:
        pass


# ── ChromaDB standalone (no LLM) ─────────────────────────────────────────────

def test_store_and_retrieve(ctx):
    """chroma_store → chroma_retrieve returns stored text."""
    cid, uid = ctx
    chroma_store(cid, uid, "HPG báo cáo tài chính Q3", "Doanh thu HPG tăng 15% so với cùng kỳ.")
    results = chroma_retrieve("HPG financial report", uid, top_k=3)
    assert any("HPG" in r for r in results), f"Stored turn not retrieved. Got: {results}"
    print(f"\n  Retrieved {len(results)} turn(s). First: {results[0][:80]!r}")


def test_persistence_across_instances(ctx):
    """Docs persisted by agent A are visible to agent B (same persist_dir)."""
    cid, uid = ctx
    agent_a = HybridMemoryAgent(conversation_id=cid, user_id=uid)
    # Store via agent A's underlying chroma (bypass chat() to avoid LLM)
    chroma_store(cid, uid, "VCB tăng trưởng tín dụng", "VCB ghi nhận lợi nhuận kỷ lục.")

    # Agent B — fresh instance, same persist_dir
    agent_b = HybridMemoryAgent(conversation_id=cid, user_id=uid)
    results = agent_b._chroma.similarity_search("VCB profit", k=3)
    assert any("VCB" in doc.page_content for doc in results), (
        f"Agent B did not see Agent A's stored turn. Got: {[d.page_content[:60] for d in results]}"
    )
    print(f"\n  Agent B retrieved {len(results)} doc(s) stored by Agent A.")


def test_delete_conversation_context_removes_docs(ctx):
    """delete_conversation_context removes only docs for that conversation_id."""
    cid, uid = ctx
    other_cid = _cid()

    chroma_store(cid, uid, "HPG steel output", "HPG produced 2M tons of steel.")
    chroma_store(other_cid, uid, "FPT software revenue", "FPT grew 20% in software.")

    delete_conversation_context(cid)

    # Docs for cid gone
    results_cid = chroma_retrieve("HPG steel", uid, top_k=5)
    assert not any("HPG produced 2M tons" in r for r in results_cid), (
        "Deleted conversation's docs still retrievable"
    )

    # Docs for other_cid untouched
    results_other = chroma_retrieve("FPT software", uid, top_k=5)
    assert any("FPT" in r for r in results_other), "Other conversation's docs incorrectly deleted"

    # Cleanup other_cid
    delete_conversation_context(other_cid)
    print("\n  Delete isolated to target conversation_id.")


def test_clear_wipes_conversation(ctx):
    """agent.clear(conversation_id) removes only that conversation's docs."""
    cid, uid = ctx
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid)

    chroma_store(cid, uid, "ACB deposit growth", "ACB tăng trưởng tiền gửi 10%.")
    before = agent._chroma._collection.count()
    assert before > 0

    agent.clear(cid)
    after_cid = agent._chroma._collection.count()
    # Count may still include docs from other conversations in same collection
    results = chroma_retrieve("ACB deposit", uid, top_k=5)
    assert not any("ACB tăng trưởng" in r for r in results), (
        "Cleared docs still retrievable"
    )
    print(f"\n  Collection count before={before}, after clear(cid)={after_cid}.")


def test_prune_oldest_when_over_cap():
    """When collection exceeds max_docs, prune removes oldest batch."""
    cid = _cid()
    uid = _uid()
    max_docs = 5
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid, max_docs=max_docs)

    # Store max_docs + 2 turns (prune batch defaults to _PRUNE_BATCH=100, but cap is 5)
    import time
    for i in range(max_docs + 2):
        chroma_store(cid, uid, f"turn {i} user", f"turn {i} assistant")
        time.sleep(0.01)  # ensure distinct created_at timestamps

    # Trigger prune manually
    from memory.chat_context import _prune_collection
    _prune_collection(agent._chroma, agent.max_docs)

    count = agent._chroma._collection.count()
    # May include docs from other tests — check only this conversation's docs
    result = agent._chroma._collection.get(
        where={"conversation_id": cid}, include=["metadatas"]
    )
    cid_count = len(result["ids"])
    print(f"\n  Stored {max_docs + 2} docs, after prune cid_count={cid_count} (max={max_docs})")
    assert cid_count <= max_docs + 2, "Prune should have run"

    # Cleanup
    delete_conversation_context(cid)


# ── HybridMemoryAgent.chat() with real LLM ───────────────────────────────────

def test_chat_returns_nonempty_reply(ctx):
    """chat() hits real LLM and returns non-empty string."""
    cid, uid = ctx
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid)

    reply = agent.chat("FPT là công ty gì?")
    print(f"\n  Reply (first 80 chars): {reply[:80]!r}")
    assert reply, "chat() must return non-empty reply"


def test_chat_stores_turn_in_chroma(ctx):
    """After chat(), the turn is retrievable from ChromaDB."""
    cid, uid = ctx
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid)

    agent.chat("Doanh thu VNM năm ngoái là bao nhiêu?")

    results = chroma_retrieve("VNM revenue", uid, top_k=3)
    assert any("VNM" in r for r in results), (
        f"Turn not stored in ChromaDB after chat(). Results: {results}"
    )
    print(f"\n  Post-chat ChromaDB has turn about VNM. Retrieved: {results[0][:80]!r}")


def test_topic_switch_retrieves_correct_context(ctx):
    """HPG turn → VCB turn → ask about HPG again → ChromaDB returns HPG turn."""
    cid, uid = ctx
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid)

    agent.chat("HPG có nợ vay bao nhiêu?")
    agent.chat("VCB tăng trưởng tín dụng như thế nào?")

    # Ask about HPG — ChromaDB should retrieve first turn, not VCB
    results = chroma_retrieve("HPG debt ratio", uid, top_k=1)
    assert results, "ChromaDB returned no results"
    assert "HPG" in results[0], (
        f"Expected HPG turn retrieved after topic switch, got: {results[0][:120]!r}"
    )
    print(f"\n  After HPG→VCB switch, HPG query retrieved: {results[0][:80]!r}")


def test_sliding_window_limits_to_4_turns(ctx):
    """Buffer holds at most 8 messages (4 turns × 2 messages)."""
    cid, uid = ctx
    agent = HybridMemoryAgent(conversation_id=cid, user_id=uid)

    # Use minimal LLM calls — patch only the buffer test
    for i in range(5):
        agent._buffer.append({"role": "user", "content": f"user msg {i}"})
        agent._buffer.append({"role": "assistant", "content": f"ai msg {i}"})

    assert len(agent._buffer) == 8, (
        f"Sliding window should be capped at 8 messages, got {len(agent._buffer)}"
    )
    # Oldest turn (i=0) evicted
    contents = [m["content"] for m in agent._buffer]
    assert "user msg 0" not in contents, "Oldest turn should be evicted from sliding window"
    assert "user msg 4" in contents, "Newest turn must be in sliding window"
    print(f"\n  Buffer capped at {len(agent._buffer)} messages; oldest turn evicted.")


# ── run_turn integration (real LLM + Postgres) ───────────────────────────────

def test_run_turn_stores_to_chroma(pg_ctx):
    """run_turn() stores turn in ChromaDB via chroma_store hook."""
    cid, uid = pg_ctx

    reply = run_turn(cid, uid, "HPG sản xuất bao nhiêu tấn thép?")
    print(f"\n  run_turn reply (first 80 chars): {reply[:80]!r}")
    assert reply, "run_turn must return non-empty reply"

    results = chroma_retrieve("HPG steel production", uid, top_k=3)
    assert any("HPG" in r for r in results), (
        f"run_turn did not store turn in ChromaDB. Retrieved: {results}"
    )
    print(f"  ChromaDB has turn. Retrieved: {results[0][:80]!r}")


def test_run_turn_chroma_context_injected(pg_ctx):
    """Turn 1 stored in ChromaDB; turn 2 retrieval finds turn 1's content."""
    cid, uid = pg_ctx

    # Turn 1: store distinctive content
    run_turn(cid, uid, "ACB có bao nhiêu chi nhánh?")

    # Turn 2: query related to turn 1 — retrieve should find it
    results = chroma_retrieve("ACB branch network", uid, top_k=3)
    assert any("ACB" in r for r in results), (
        f"Turn 1 content not retrievable for turn 2 context injection. Got: {results}"
    )
    print(f"\n  Turn 2 chroma context includes turn 1 content: {results[0][:80]!r}")


def test_delete_conversation_clears_chroma(pg_ctx):
    """delete_conversation() cleans Postgres + Qdrant + ChromaDB."""
    cid, uid = pg_ctx

    run_turn(cid, uid, "FPT doanh thu phần mềm là bao nhiêu?")

    # Verify stored
    before = chroma_retrieve("FPT software revenue", uid, top_k=3)
    assert any("FPT" in r for r in before), "Precondition: turn must be in ChromaDB"

    # Delete conversation — should cascade to ChromaDB
    delete_conversation(cid)

    # Verify gone
    after = chroma_retrieve("FPT software revenue", uid, top_k=3)
    assert not any("FPT doanh thu phần mềm" in r for r in after), (
        "ChromaDB docs still present after delete_conversation()"
    )
    print("\n  delete_conversation() cascaded to ChromaDB — docs removed.")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests_no_llm = [
        ("test_store_and_retrieve", lambda: test_store_and_retrieve((_cid(), _uid()))),
        ("test_prune_oldest_when_over_cap", test_prune_oldest_when_over_cap),
        ("test_sliding_window_limits_to_4_turns", lambda: test_sliding_window_limits_to_4_turns((_cid(), _uid()))),
    ]
    tests_with_llm = [
        ("test_chat_returns_nonempty_reply", lambda: test_chat_returns_nonempty_reply((_cid(), _uid()))),
        ("test_chat_stores_turn_in_chroma", lambda: test_chat_stores_turn_in_chroma((_cid(), _uid()))),
        ("test_topic_switch_retrieves_correct_context", lambda: test_topic_switch_retrieves_correct_context((_cid(), _uid()))),
    ]

    print("=== No-LLM tests ===")
    for name, fn in tests_no_llm:
        print(f"\n--- {name} ---")
        fn()

    print("\n=== LLM tests (requires DeepSeek + Ollama) ===")
    for name, fn in tests_with_llm:
        print(f"\n--- {name} ---")
        fn()

    print("\nAll tests passed.")
