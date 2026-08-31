"""
memory/turn_handler.py — Orchestrates one conversation turn (Bài 28 + 29 + 31).

run_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → str (assistant reply)
stream_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → AsyncIterator[str] (SSE)

Flow (stream_turn):
  1. load_history / load_user_memory / retrieve_similar (memory load)
  2. Fast cache check (keyword classify only — no LLM)
  3. build_graph().invoke(make_initial_state(query, conversation_id, user_id))
       Graph handles: pending merge, classify_hybrid, verify_context, all intent dispatch
  4. Handle result:
       needs_clarification → emit clarification (pending already saved by verify_context)
       intent == "conversation" → stream LLM directly
       else → emit report
  5. Cache set + save_turn + extract_preferences

finish_conversation(conversation_id, user_id, first_question, summary, conclusion)
  → called by API after last turn to store episode in Qdrant
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import AsyncIterator

log = logging.getLogger(__name__)

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

    # ── Route + cache check ───────────────────────────────────────────────────
    from agents.classifier import classify_hybrid as _route_classify
    _route = _route_classify(user_message)

    from core.cache import make_cache_key, cache_get, cache_set
    _ck = make_cache_key(tenant_id, user_message, _route.ticker or "", _route.intent, history)
    if _ck is not None:
        cached_reply, _tier = cache_get(_ck)
        if cached_reply:
            save_turn(conversation_id, user_message, cached_reply)
            return cached_reply

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
        max_tokens=3500,
    )
    assistant_reply = response.text.strip()

    # Persist turn
    save_turn(conversation_id, user_message, assistant_reply)

    # Cache set — skip clarification replies (ticker=None on ticker-required intent)
    _TICKER_REQUIRED_SET = {
        "price_action", "technical_analysis", "rag_qa",
        "news_sentiment", "investment_case",
    }
    _is_clarification = (_route.ticker is None and _route.intent in _TICKER_REQUIRED_SET)
    if _ck is not None and not _is_clarification:
        cache_set(_ck, assistant_reply)

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


HEARTBEAT_INTERVAL = 15.0


def _sse_status(step: str, **extra) -> str:
    return f"event: status\ndata: {json.dumps({'step': step, **extra})}\n\n"


def _sse_chunk(text: str) -> str:
    return f"data: {json.dumps({'text': text})}\n\n"


def _sse_done(length: int, agent: str) -> str:
    return f"event: done\ndata: {json.dumps({'saved': True, 'length': length, 'agent': agent})}\n\n"


async def _stream_via_queue(
    client,
    lm_messages: list,
    system_prompt: str,
) -> AsyncIterator[str]:
    """Wrap sync client.stream() in thread+queue, yield SSE data chunks.
    Propagates asyncio.CancelledError — caller must handle it."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stream_error: list[Exception] = []

    def _producer() -> None:
        def _safe_put(item) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass

        try:
            for chunk in client.stream(
                messages=lm_messages,
                system=system_prompt,
                max_tokens=3000,
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
            yield _sse_chunk(chunk)
    except asyncio.CancelledError:
        thread.join(timeout=1)
        raise

    if stream_error:
        yield f"event: error\ndata: {json.dumps({'error': str(stream_error[0])})}\n\n"

    # stash full text for caller via a special sentinel (last yielded item)
    yield f"__collected__:{json.dumps({'text': ''.join(collected)})}"


async def stream_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    tenant_id: str = "default",
    is_first_turn: bool = False,
) -> AsyncIterator[str]:
    """Async generator yielding SSE events for one conversation turn.

    Graph (agents/graph.py) is the single entry point for all agent paths —
    it handles pending merge, classify_hybrid, verify_context, and all intent dispatch.
    stream_turn handles: memory load, cache, conversation streaming, persist.

    SSE events:
      event: status   → {"step": "...", "agent": "...", ...}
      data: ...       → {"text": chunk}
      : heartbeat     → comment every 15s
      event: done     → {"saved": true, "length": N, "agent": "..."}
      event: error    → {"error": "..."}

    CancelledError (client disconnect) → turn NOT saved.
    """
    yield _sse_status("loading_history")

    history = load_history(conversation_id, limit=10)
    user_memory = load_user_memory(user_id, tenant_id, max_items=5)

    episodes: list[dict] = []
    if is_first_turn:
        try:
            from memory.episodic import retrieve_similar
            episodes = retrieve_similar(user_message, user_id, top_k=3)
        except Exception:
            episodes = []

    # ── Invoke graph ──────────────────────────────────────────────────────────
    from agents.state import make_initial_state
    from agents.graph import build_graph
    from agents.checkpointer import PostgresCheckpointer
    from langgraph.types import Command

    yield _sse_status("routing")

    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer)
    thread_config = {"configurable": {"thread_id": conversation_id}}

    # If previous turn left graph in interrupted state (waiting for clarification answer),
    # resume with the user's message instead of starting fresh.
    try:
        prior_state = await asyncio.to_thread(app.get_state, thread_config)
        is_interrupted = bool(prior_state and prior_state.next)
    except Exception:
        is_interrupted = False

    if is_interrupted:
        invoke_input = Command(resume=user_message)
    else:
        invoke_input = make_initial_state(
            user_message,
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            messages=history,
        )

    try:
        task = asyncio.create_task(
            asyncio.to_thread(app.invoke, invoke_input, thread_config)
        )
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
        final = task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        return

    # After invoke, check if graph paused at clarify_node waiting for user answer.
    try:
        after_state = await asyncio.to_thread(app.get_state, thread_config)
        if after_state and after_state.next:
            interrupts = (
                after_state.tasks[0].interrupts
                if after_state.tasks else []
            )
            question = interrupts[0].value if interrupts else "Bạn cần cung cấp thêm thông tin."
            log.info("clarify.interrupt conv=%s question=%r", conversation_id[:8], question[:60])
            yield _sse_status("streaming", agent="clarification")
            for line in question.split("\n"):
                yield _sse_chunk(line + "\n")
            try:
                save_turn(conversation_id, user_message, question)
            except Exception:
                pass
            yield _sse_done(len(question), "clarification")
            return
    except Exception:
        pass

    intent = final.get("intent", "conversation")
    ticker = final.get("ticker") or None

    yield _sse_status("routing", agent=intent, ticker=ticker)

    # Cache hit: graph returned cached report — stream it directly
    if final.get("_cache_hit"):
        cached = final.get("report", "")
        log.info("cache.hit conv=%s intent=%s ticker=%s tier=%s",
                 conversation_id[:8], intent, ticker, final.get("_cache_tier", ""))
        yield _sse_status("cache_hit", tier=final.get("_cache_tier", ""), ticker=ticker)
        for line in cached.split("\n"):
            yield _sse_chunk(line + "\n")
        try:
            save_turn(conversation_id, user_message, cached)
        except Exception:
            pass
        yield _sse_done(len(cached), f"{intent}:cache")
        return

    assistant_reply = ""

    # ── Conversation: stream LLM directly (graph returned intent="conversation") ──
    if intent == "conversation":
        system_prompt = _build_system(user_memory, episodes)
        lm_messages = [Message(role=m["role"], content=m["content"]) for m in history]
        lm_messages.append(Message(role="user", content=user_message))

        client = create_client()
        yield _sse_status("streaming", agent="conversation")

        collected: list[str] = []
        try:
            async for sse_line in _stream_via_queue(client, lm_messages, system_prompt):
                if sse_line.startswith("__collected__:"):
                    collected_text = json.loads(sse_line[len("__collected__:"):])["text"]
                    collected = [collected_text]
                else:
                    yield sse_line
        except asyncio.CancelledError:
            return

        assistant_reply = "".join(collected)

    # ── Agent report (all other intents) ──────────────────────────────────────
    else:
        report = final.get("report") or ""
        yield _sse_status("streaming", agent=intent)
        for line in report.split("\n"):
            yield _sse_chunk(line + "\n")
        assistant_reply = report

    if not assistant_reply:
        return

    # ── Persist + extract preferences ─────────────────────────────────────────
    try:
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
    except Exception:
        pass

    yield _sse_done(len(assistant_reply), intent)


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
