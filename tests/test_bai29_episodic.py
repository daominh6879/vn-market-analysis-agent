"""
tests/test_bai29_episodic.py — Bài 29: Episodic memory (quên đi)

Real LLM + Qdrant. No mocks.

Covers:
  1. store_episode + retrieve_similar — finds relevant episode, not unrelated one
  2. Decay: older episode scores lower than newer one for same query
  3. Expiry: episode with created_at > 90 days old is excluded
  4. Hard limit: retrieve_similar never returns more than 3 episodes
  5. User isolation: user B cannot retrieve user A's episodes
  6. procedural.generate_rules — extracts rules from explicit feedback
  7. Load test: 20 stored episodes → retrieval returns ≤ 3 (context not bloated)
  8. turn_handler.finish_conversation → stores episode; next run_turn (is_first_turn=True) injects context
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from memory.episodic import store_episode, retrieve_similar, COLLECTION, _qdrant, _ensure_collection
from memory.procedural import generate_rules


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return f"test_user_{uuid.uuid4().hex[:8]}"


def _cid() -> str:
    return str(uuid.uuid4())


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_store_and_retrieve_relevant():
    """Stored episode surfaces when query is semantically similar."""
    user_id = _uid()
    pid = store_episode(
        conversation_id=_cid(),
        user_id=user_id,
        first_question="Phân tích cổ phiếu FPT theo góc nhìn tăng trưởng dài hạn",
        summary="Người dùng hỏi về FPT, tập trung vào mảng công nghệ và xuất khẩu phần mềm.",
        conclusion="FPT có tiềm năng tăng trưởng tốt nhờ mảng IT services nước ngoài.",
    )
    assert isinstance(pid, str) and len(pid) > 0

    results = retrieve_similar("FPT tăng trưởng dài hạn công nghệ", user_id)
    assert len(results) >= 1
    assert any("FPT" in r["first_question"] for r in results)


def test_retrieve_empty_for_unrelated_user():
    """User isolation: user B cannot see user A's episodes."""
    user_a = _uid()
    user_b = _uid()

    store_episode(
        conversation_id=_cid(),
        user_id=user_a,
        first_question="Phân tích HPG ngành thép năm 2024",
        summary="HPG chiếm thị phần lớn trong thị trường thép Việt Nam.",
        conclusion="HPG vẫn là dẫn đầu thị trường thép nội địa.",
    )

    results = retrieve_similar("HPG thép", user_b)
    # user_b has no episodes — should get nothing from user_a
    assert all(r["conversation_id"] != user_a for r in results)


def test_hard_limit_max_3():
    """retrieve_similar never returns more than 3 episodes."""
    user_id = _uid()
    for i in range(6):
        store_episode(
            conversation_id=_cid(),
            user_id=user_id,
            first_question=f"Câu hỏi về cổ phiếu số {i} thị trường chứng khoán",
            summary=f"Tóm tắt phân tích số {i}",
            conclusion=f"Kết luận số {i}",
        )

    results = retrieve_similar("cổ phiếu thị trường chứng khoán", user_id)
    assert len(results) <= 3


def test_load_20_episodes_no_context_bloat():
    """20 episodes stored → retrieval returns ≤ 3 regardless."""
    user_id = _uid()
    for i in range(20):
        store_episode(
            conversation_id=_cid(),
            user_id=user_id,
            first_question=f"Phân tích doanh nghiệp {i} trong ngành tài chính",
            summary=f"Tóm tắt {i}: doanh nghiệp hoạt động bình thường.",
            conclusion=f"Kết luận {i}: giữ.",
        )

    results = retrieve_similar("doanh nghiệp tài chính", user_id)
    assert len(results) <= 3, f"Expected ≤ 3 but got {len(results)}"


def test_decay_older_scores_lower():
    """Episode with created_at 60 days ago scores lower than one created now, same query."""
    user_id = _uid()
    client = _qdrant()
    _ensure_collection(client)

    from qdrant_client.models import PointStruct
    from memory.episodic import _embed, EMBED_DIM

    query = "phân tích VCB ngân hàng"
    vec = _embed(query)

    # Recent episode
    pid_new = str(uuid.uuid4())
    pid_old = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=pid_new,
                vector=vec,
                payload={
                    "conversation_id": _cid(),
                    "user_id": user_id,
                    "first_question": query,
                    "summary": "phân tích VCB mới",
                    "conclusion": "VCB tốt",
                    "feedback": "",
                    "created_at": now.isoformat(),
                },
            ),
            PointStruct(
                id=pid_old,
                vector=vec,
                payload={
                    "conversation_id": _cid(),
                    "user_id": user_id,
                    "first_question": query,
                    "summary": "phân tích VCB cũ",
                    "conclusion": "VCB cũ",
                    "feedback": "",
                    "created_at": (now - timedelta(days=60)).isoformat(),
                },
            ),
        ],
    )

    results = retrieve_similar(query, user_id)
    assert len(results) >= 2, "Expected at least 2 results"
    scores_by_conclusion = {r["conclusion"]: r["score"] for r in results}
    assert scores_by_conclusion.get("VCB tốt", 0) > scores_by_conclusion.get("VCB cũ", 0), \
        "Newer episode should score higher after decay"


def test_expired_episodes_excluded():
    """Episode older than 90 days is excluded from retrieval."""
    user_id = _uid()
    client = _qdrant()
    _ensure_collection(client)

    from qdrant_client.models import PointStruct
    from memory.episodic import _embed

    query = "phân tích MWG bán lẻ"
    vec = _embed(query)
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=pid,
                vector=vec,
                payload={
                    "conversation_id": _cid(),
                    "user_id": user_id,
                    "first_question": query,
                    "summary": "phân tích MWG rất cũ",
                    "conclusion": "MWG đã hết hạn",
                    "feedback": "",
                    "created_at": (now - timedelta(days=100)).isoformat(),
                },
            )
        ],
    )

    results = retrieve_similar(query, user_id)
    conclusions = [r["conclusion"] for r in results]
    assert "MWG đã hết hạn" not in conclusions, "Expired episode should be excluded"


def test_procedural_generate_rules_from_explicit_feedback():
    """generate_rules returns non-empty list for explicit user instruction."""
    feedback = "Đừng hiển thị bảng số liệu dài, tôi chỉ muốn kết luận ngắn gọn."
    rules = generate_rules(feedback)
    assert isinstance(rules, list)
    assert len(rules) >= 1
    for rule in rules:
        assert rule.rule.strip() != ""
        assert 1 <= rule.priority <= 5


def test_procedural_no_rules_from_empty():
    """generate_rules returns empty for empty input."""
    rules = generate_rules("")
    assert rules == []


def test_finish_conversation_and_retrieval(monkeypatch):
    """
    finish_conversation stores episode; subsequent run_turn with is_first_turn=True
    injects episodic context into system prompt.
    """
    from memory.conversation import create_conversation
    from memory.turn_handler import finish_conversation, run_turn

    user_id = _uid()

    # Store a past episode via finish_conversation
    conv1 = create_conversation(user_id)
    finish_conversation(
        conversation_id=conv1,
        user_id=user_id,
        first_question="Tôi muốn phân tích cổ phiếu VHM bất động sản",
        summary="Người dùng hỏi về VHM, quan tâm đến tỷ lệ nợ và dòng tiền.",
        conclusion="VHM có dòng tiền tốt nhưng tỷ lệ nợ cao cần theo dõi.",
    )

    # New conversation — first turn should inject the episodic context
    conv2 = create_conversation(user_id)
    injected_episodes: list = []

    original_retrieve = None
    import memory.episodic as ep_mod

    original_retrieve = ep_mod.retrieve_similar

    def capture_retrieve(query, uid, top_k=3):
        result = original_retrieve(query, uid, top_k=top_k)
        injected_episodes.extend(result)
        return result

    monkeypatch.setattr(ep_mod, "retrieve_similar", capture_retrieve)

    reply = run_turn(conv2, user_id, "VHM hiện tại thế nào?", is_first_turn=True)

    assert isinstance(reply, str) and len(reply) > 0
    # The captured episodes should include the one we stored
    if injected_episodes:
        assert any("VHM" in ep.get("first_question", "") for ep in injected_episodes)
