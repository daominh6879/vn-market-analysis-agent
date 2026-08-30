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

import uuid

from langfuse import observe

from llm.factory import create_client
from tracing import current_request_id
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
        max_tokens=3500,
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


async def _run_blocking_agent(fn, *args, **kwargs) -> str:
    """Run a blocking sync function in thread pool, return result string.
    Sends heartbeat-style yields via an asyncio.Event approach — caller
    separately yields heartbeats while awaiting."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Intent dispatcher (traced as Langfuse parent span) ────────────────────────

@observe(name="agent.turn")
def _dispatch_intent(
    route,
    user_message: str,
    user_id: str,
    conversation_id: str,
) -> str:
    """Single sync dispatch point for all intent types. Traced as Langfuse parent span.
    All nested intent.run() and tool @observe calls become children of this span."""
    # Set request_id so all nested instrument_tool calls share it in traces/latest.jsonl
    rid = f"{conversation_id[:8]}-{uuid.uuid4().hex[:6]}"
    current_request_id.set(rid)

    try:
        from langfuse import get_client
        get_client().update_current_trace(
            session_id=conversation_id,
            user_id=user_id,
            input=user_message,
            metadata={"intent": route.intent, "ticker": route.ticker, "request_id": rid},
        )
    except Exception:
        pass

    ticker = route.ticker or "HPG"

    if route.intent == "price_action":
        from agents.intents.price_action import run
        return run(ticker, user_message)

    if route.intent == "technical_analysis":
        from agents.intents.technical import run
        return run(ticker, user_message)

    if route.intent == "fundamentals":
        from agents.intents.fundamentals import run
        return run(route.ticker, user_message)

    if route.intent == "macro_sector":
        from agents.intents.macro_sector import run
        return run(route.ticker, user_message)

    if route.intent == "news_sentiment":
        from agents.intents.news_sentiment import run
        return run(route.ticker, user_message)

    if route.intent == "investment_case":
        from agents.intents.investment_case import run
        return run(ticker, user_message)

    if route.intent == "screening":
        from agents.intents.screening import run
        return run(route.ticker, user_message)

    if route.intent == "qa_document":
        from rag.qa import answer as qa_answer
        return qa_answer(user_message, ticker=route.ticker)

    return ""


async def stream_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    tenant_id: str = "default",
    is_first_turn: bool = False,
) -> AsyncIterator[str]:
    """Async generator yielding SSE events for one conversation turn.

    Routes query to the right agent before streaming:
      ticker_analysis → agents/graph.py  (price + technical + news)
      market_brief    → agents/market_brief_graph.py (full market overview)
      qa_document     → rag/qa.py (RAG / SQL)
      conversation    → direct LLM stream (memory-aware)

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

    # ── Route query ───────────────────────────────────────────────────────────
    from agents.query_router import classify_hybrid as route_classify
    route = route_classify(user_message)

    yield _sse_status("routing", agent=route.intent, reason=route.reason,
                      ticker=route.ticker)

    assistant_reply = ""

    # ── All intent paths (traced via _dispatch_intent) ───────────────────────
    _AGENT_INTENTS = {
        "price_action", "technical_analysis", "fundamentals",
        "macro_sector", "news_sentiment", "investment_case",
        "screening", "qa_document",
    }
    _MARKET_BRIEF_INTENTS = {"market_brief"}

    if route.intent in _AGENT_INTENTS:
        yield _sse_status("collecting_data", ticker=route.ticker, intent=route.intent)
        try:
            task = asyncio.create_task(
                _run_blocking_agent(_dispatch_intent, route, user_message, user_id, conversation_id)
            )
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
            report = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            return
        yield _sse_status("streaming", agent=route.intent)
        for line in report.split("\n"):
            yield _sse_chunk(line + "\n")
        assistant_reply = report

    # ── market_brief path ─────────────────────────────────────────────────────
    elif route.intent in _MARKET_BRIEF_INTENTS:
        yield _sse_status("collecting_market_data")
        try:
            from datetime import date as date_cls

            def _run_brief():
                from agents.market_brief_graph import build_brief_graph, make_initial_state as mb_init
                app = build_brief_graph()
                initial = mb_init(date=str(date_cls.today()), output_path="")
                final = app.invoke(initial)
                return final.get("report_text") or "[Không có báo cáo thị trường]"

            task = asyncio.create_task(_run_blocking_agent(_run_brief))
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
            report = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            return
        yield _sse_status("streaming", agent="market_brief")
        for line in report.split("\n"):
            yield _sse_chunk(line + "\n")
        assistant_reply = report

    # ── conversation path (LLM stream) ────────────────────────────────────────
    else:
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

    if not assistant_reply:
        return

    # ── Persist + extract preferences (all paths) ─────────────────────────────
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
        pass  # persistence failure must not suppress the done event

    yield _sse_done(len(assistant_reply), route.intent)


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
