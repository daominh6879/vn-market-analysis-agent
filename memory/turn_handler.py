"""
memory/turn_handler.py — Orchestrates one conversation turn (Bài 28 + 29).

run_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → str (assistant reply)

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
