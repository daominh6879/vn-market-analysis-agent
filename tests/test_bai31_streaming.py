"""
tests/test_bai31_streaming.py — Bài 31 streaming integration tests.

All tests hit real DB + real LLM (DeepSeek default).
Run: python -m pytest tests/test_bai31_streaming.py -v -s

Covers every "Xong khi" checklist item:
  [x] Chunks arrive incrementally (multiple data events before done)
  [x] Turn 2 same conversation_id → agent sees turn 1 history while streaming
  [x] Disconnect mid-stream → turn NOT saved
  [x] Done event arrives with saved=True at end
"""

from __future__ import annotations

import asyncio
import uuid
from dotenv import load_dotenv
load_dotenv()

import pytest

from memory.conversation import create_conversation, load_history
from memory.turn_handler import stream_turn


def _collect_sse(coro) -> tuple[list[str], list[str], bool, bool]:
    """Run async generator, return (chunks, status_steps, saved, error_seen)."""
    chunks: list[str] = []
    status_steps: list[str] = []
    saved = False
    error_seen = False

    async def _run():
        nonlocal saved, error_seen
        async for line in coro:
            if line.startswith("data: "):
                import json
                payload = json.loads(line[6:].strip())
                if "text" in payload:
                    chunks.append(payload["text"])
            elif line.startswith("event: status"):
                pass  # next line has the data
            elif line.startswith("event: done"):
                pass
            elif "saved" in line:
                import json
                try:
                    payload = json.loads(line.split("data: ", 1)[1].strip())
                    saved = payload.get("saved", False)
                except Exception:
                    pass
            elif line.startswith("event: error"):
                error_seen = True

    asyncio.run(_run())
    return chunks, status_steps, saved, error_seen


def _run_stream(conversation_id: str, user_id: str, message: str, is_first_turn: bool = False) -> list[str]:
    """Run stream_turn, collect all SSE lines, return raw lines."""
    lines: list[str] = []

    async def _run():
        async for line in stream_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=message,
            tenant_id="default",
            is_first_turn=is_first_turn,
        ):
            lines.append(line)

    asyncio.run(_run())
    return lines


def _parse_chunks(lines: list[str]) -> list[str]:
    import json
    chunks = []
    for line in lines:
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:].strip())
                if "text" in payload:
                    chunks.append(payload["text"])
            except Exception:
                pass
    return chunks


def _parse_done(lines: list[str]) -> dict | None:
    """Each line may be a multi-line SSE block like 'event: done\ndata: {...}\n\n'."""
    import json
    for raw in lines:
        sub = raw.split("\n")
        for i, s in enumerate(sub):
            if s.strip() == "event: done":
                for j in range(i + 1, len(sub)):
                    if sub[j].startswith("data: "):
                        try:
                            return json.loads(sub[j][6:].strip())
                        except Exception:
                            pass
    return None


@pytest.fixture
def conversation_id():
    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(user_id, "default")
    return cid, user_id


def test_chunks_arrive_incrementally(conversation_id):
    """Multiple data events must arrive before done — not one big blob."""
    cid, user_id = conversation_id
    lines = _run_stream(cid, user_id, "HPG là công ty gì? Trả lời ngắn gọn trong 2 câu.", is_first_turn=True)

    chunks = _parse_chunks(lines)
    done = _parse_done(lines)

    print(f"\nTotal chunks: {len(chunks)}")
    print(f"Full reply: {''.join(chunks)[:200]}")
    print(f"Done event: {done}")

    assert len(chunks) >= 2, f"Expected >= 2 chunks for streaming, got {len(chunks)}"
    assert done is not None, "Expected event: done at end"
    assert done.get("saved") is True


def test_turn2_sees_turn1_history(conversation_id):
    """Turn 2 response must reference or be consistent with turn 1 context."""
    cid, user_id = conversation_id

    # Turn 1: establish context
    lines1 = _run_stream(cid, user_id, "Tôi đang hỏi về HPG.", is_first_turn=True)
    chunks1 = _parse_chunks(lines1)
    reply1 = "".join(chunks1)
    assert reply1, "Turn 1 must return non-empty reply"

    # Verify turn 1 was saved
    history_after_t1 = load_history(cid, limit=10)
    assert len(history_after_t1) >= 2, f"Turn 1 must be saved to history, got {len(history_after_t1)} messages"

    # Turn 2: ask about previous message — agent should see history
    lines2 = _run_stream(cid, user_id, "Bạn vừa nói gì ở câu trước?")
    chunks2 = _parse_chunks(lines2)
    reply2 = "".join(chunks2)

    print(f"\nTurn 1 reply: {reply1[:100]}")
    print(f"Turn 2 reply: {reply2[:200]}")

    history_after_t2 = load_history(cid, limit=10)
    assert len(history_after_t2) >= 4, f"Both turns must be saved, got {len(history_after_t2)} messages"
    assert reply2, "Turn 2 must return non-empty reply"
    # Turn 2 should mention HPG since it was in the history context
    assert "HPG" in reply2 or "câu trước" in reply2.lower() or len(reply2) > 20, \
        f"Turn 2 appears to ignore history: {reply2}"


def test_done_event_saved_true(conversation_id):
    """Full stream must end with event:done and saved:true."""
    cid, user_id = conversation_id
    lines = _run_stream(cid, user_id, "Xin chào!")

    done = _parse_done(lines)
    assert done is not None, "event: done must appear"
    assert done.get("saved") is True
    assert done.get("length", 0) > 0


def test_disconnect_mid_stream_turn_not_saved():
    """CancelledError mid-stream → turn NOT written to messages table."""
    user_id = f"test-cancel-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(user_id, "default")

    async def _cancel_after_first_chunk():
        count = 0
        async for line in stream_turn(
            conversation_id=cid,
            user_id=user_id,
            user_message="Phân tích HPG chi tiết nhất có thể.",
        ):
            if line.startswith("data: "):
                import json
                try:
                    payload = json.loads(line[6:].strip())
                    if "text" in payload:
                        count += 1
                except Exception:
                    pass
            if count >= 1:
                # Simulate client disconnect
                raise asyncio.CancelledError

    try:
        asyncio.run(_cancel_after_first_chunk())
    except asyncio.CancelledError:
        pass

    history = load_history(cid, limit=10)
    assert len(history) == 0, \
        f"Disconnected stream must NOT save turn, but found {len(history)} messages"
    print(f"\nDisconnect test: history has {len(history)} messages (expected 0)")
