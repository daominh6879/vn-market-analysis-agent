"""
memory/turn_handler.py — Orchestrates one conversation turn (Bài 28 + 29 + 31).

run_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → str (assistant reply)
stream_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → AsyncIterator[str] (SSE)

Flow:
  1. load_history(conversation_id)             → inject into LLM messages
  2. load_user_memory(user_id)                 → inject into system prompt
  3. [Bài 29] retrieve_similar episodic memory → inject into system prompt (first turn only)
  4. LLM generates reply
  5. save_turn(...)                            → persist (user, assistant) to DB
  6. extract_preferences(turn_messages)        → save items with confidence >= 0.7

finish_conversation(conversation_id, user_id, first_question, summary, conclusion)
  → called by API after last turn to store episode in Qdrant
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncIterator

from llm.factory import create_client
from llm.types import Message
from memory.conversation import load_history, save_turn
from memory.extractor import extract_preferences
from memory.reader import load_user_memory, save_memory_item

_BASE_SYSTEM = """Bạn là trợ lý phân tích tài chính chuyên về thị trường chứng khoán Việt Nam.
Trả lời bằng tiếng Việt. Dựa vào lịch sử hội thoại và sở thích của người dùng đã được ghi nhớ."""


def _build_system(user_memory: list[dict], episodes: list[dict] | None = None) -> str:
    parts = [_BASE_SYSTEM]

    if user_memory:
        memory_lines = "\n".join(
            f"- {m['key']}: {m['value']} (confidence={m['confidence']:.2f})"
            for m in user_memory
        )
        parts.append(f"\nSở thích đã biết của người dùng:\n{memory_lines}")

    if episodes:
        ep_lines = []
        for ep in episodes:
            ep_lines.append(
                f"- [{ep['days_old']} ngày trước] {ep['first_question']}: {ep['conclusion']}"
            )
        parts.append(f"\nCác cuộc trò chuyện liên quan trước đây:\n" + "\n".join(ep_lines))

    return "\n".join(parts)


def run_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    tenant_id: str = "default",
    is_first_turn: bool = False,
) -> str:
    """Run one turn, persist history, extract and save preferences. Returns assistant reply."""
    history = load_history(conversation_id, limit=10)
    user_memory = load_user_memory(user_id, tenant_id, max_items=5)

    # Inject episodic context only on first turn of a new conversation
    episodes: list[dict] = []
    if is_first_turn:
        try:
            from memory.episodic import retrieve_similar
            episodes = retrieve_similar(user_message, user_id, top_k=3)
        except Exception:
            episodes = []

    system_prompt = _build_system(user_memory, episodes)

    # Build LLM messages: history + new user message
    lm_messages = [Message(role=m["role"], content=m["content"]) for m in history]
    lm_messages.append(Message(role="user", content=user_message))

    client = create_client()
    response = client.generate(
        messages=lm_messages,
        system=system_prompt,
        max_tokens=1024,
    )
    assistant_reply = response.text.strip()

    # Persist turn
    save_turn(conversation_id, user_message, assistant_reply)

    # Extract preferences from this turn only (run AFTER turn completes)
    turn_messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_reply},
    ]
    preferences = extract_preferences(turn_messages)
    for pref in preferences:
        save_memory_item(
            user_id=user_id,
            tenant_id=tenant_id,
            key=pref.key,
            value=pref.value,
            confidence=pref.confidence,
            source_message=pref.source_message,
        )

    return assistant_reply


async def stream_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    tenant_id: str = "default",
    is_first_turn: bool = False,
) -> AsyncIterator[str]:
    """Async generator yielding SSE events for one conversation turn.

    SSE event types:
      event: status   → {"step": "loading_history" | "streaming"}
      data: ...       → {"text": chunk}  (content chunks)
      : heartbeat     → comment every 15 s if LLM is slow
      event: done     → {"saved": true, "length": N}
      event: error    → {"error": "..."}

    On asyncio.CancelledError (client disconnect): task cancelled, turn NOT saved.
    """
    HEARTBEAT_INTERVAL = 15.0

    yield f"event: status\ndata: {json.dumps({'step': 'loading_history'})}\n\n"

    history = load_history(conversation_id, limit=10)
    user_memory = load_user_memory(user_id, tenant_id, max_items=5)

    episodes: list[dict] = []
    if is_first_turn:
        try:
            from memory.episodic import retrieve_similar
            episodes = retrieve_similar(user_message, user_id, top_k=3)
        except Exception:
            episodes = []

    system_prompt = _build_system(user_memory, episodes)
    lm_messages = [Message(role=m["role"], content=m["content"]) for m in history]
    lm_messages.append(Message(role="user", content=user_message))

    client = create_client()
    yield f"event: status\ndata: {json.dumps({'step': 'streaming'})}\n\n"

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stream_error: list[Exception] = []

    def _producer() -> None:
        def _safe_put(item) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass  # loop closed (client disconnected)

        try:
            for chunk in client.stream(
                messages=lm_messages,
                system=system_prompt,
                max_tokens=1024,
            ):
                _safe_put(chunk)
        except Exception as exc:
            stream_error.append(exc)
        finally:
            _safe_put(None)

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    collected: list[str] = []
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue

            if chunk is None:
                break
            collected.append(chunk)
            yield f"data: {json.dumps({'text': chunk})}\n\n"

    except asyncio.CancelledError:
        # Client disconnected — do NOT save turn
        thread.join(timeout=1)
        return

    if stream_error:
        yield f"event: error\ndata: {json.dumps({'error': str(stream_error[0])})}\n\n"
        return

    assistant_reply = "".join(collected)
    save_turn(conversation_id, user_message, assistant_reply)

    turn_messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_reply},
    ]
    preferences = extract_preferences(turn_messages)
    for pref in preferences:
        save_memory_item(
            user_id=user_id,
            tenant_id=tenant_id,
            key=pref.key,
            value=pref.value,
            confidence=pref.confidence,
            source_message=pref.source_message,
        )

    yield f"event: done\ndata: {json.dumps({'saved': True, 'length': len(assistant_reply)})}\n\n"


def finish_conversation(
    conversation_id: str,
    user_id: str,
    first_question: str,
    summary: str,
    conclusion: str,
    feedback: str = "",
) -> str:
    """Store a completed conversation as an episodic memory in Qdrant. Returns point id."""
    from memory.episodic import store_episode
    return store_episode(
        conversation_id=conversation_id,
        user_id=user_id,
        first_question=first_question,
        summary=summary,
        conclusion=conclusion,
        feedback=feedback,
    )
