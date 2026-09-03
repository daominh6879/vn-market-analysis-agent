"""
memory/turn_handler.py — Orchestrates one conversation turn (Bài 28 + 29 + 31).

run_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → str (assistant reply)
stream_turn(conversation_id, user_id, tenant_id, user_message, is_first_turn) → AsyncIterator[str] (SSE)

Flow (stream_turn) — LLM on top:
  1. load_history / load_user_memory / retrieve_similar (memory load)
  2. If graph is interrupted (awaiting clarification answer) → resume graph directly
  3. llm_route(query, history, system_prompt) — single LLM call decides:
       type="agent" → invoke graph with pre-set intent/ticker
       type="text"  → stream LLM reply directly, skip graph
  4. Agent path: graph handles cache, clarify interrupt, all intent dispatch
  5. save_turn + extract_preferences

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
Trả lời bằng tiếng Việt. Dựa vào lịch sử hội thoại và sở thích của người dùng đã được ghi nhớ.
Dữ liệu thị trường chỉ đến từ công cụ phân tích (needs_agent_run) — lịch sử hội thoại chứa kết quả cũ, không dùng để trả lời câu hỏi mới về bất kỳ mã/ngành nào."""


def _build_system(
    user_memory: list[dict],
    episodes: list[dict] | None = None,
    chroma_turns: list[str] | None = None,
) -> str:
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

    if chroma_turns:
        parts.append("\nCác lượt hội thoại liên quan:\n" + "\n---\n".join(chroma_turns))

    return "\n".join(parts)


def run_turn(
    conversation_id: str,
    user_id: str,
    user_message: str,
    tenant_id: str = "default",
    is_first_turn: bool = False,
) -> str:
    """Synchronous turn — uses llm_route + graph (same logic as stream_turn, no streaming)."""
    history = load_history(conversation_id, limit=10)
    user_memory = load_user_memory(user_id, tenant_id, max_items=5)

    from memory.retrieval_gate import should_retrieve
    do_retrieve, refined_query = should_retrieve(user_message)

    episodes: list[dict] = []
    if is_first_turn and do_retrieve:
        try:
            from memory.episodic import retrieve_similar
            episodes = retrieve_similar(refined_query, user_id, top_k=3)
        except Exception:
            episodes = []

    chroma_turns: list[str] = []
    if do_retrieve:
        try:
            from memory.chat_context import chroma_retrieve
            chroma_turns = chroma_retrieve(refined_query, user_id, top_k=3)
        except Exception:
            pass

    system_prompt = _build_system(user_memory, episodes, chroma_turns=chroma_turns)
    client = create_client()

    from agents.conversation_router import llm_route
    route = llm_route(user_message, history, system_prompt, client)

    if route.get("type") == "agent":
        from agents.state import make_initial_state
        from agents.graph import build_graph
        from agents.checkpointer import PostgresCheckpointer

        checkpointer = PostgresCheckpointer()
        app = build_graph(checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": conversation_id}}

        query = route.get("query") or user_message
        agent_state = make_initial_state(
            query,
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            messages=history,
        )
        agent_state["intent"] = route["intent"]
        agent_state["ticker"] = route.get("ticker", "")
        agent_state["original_query"] = user_message

        final = app.invoke(agent_state, thread_config)
        assistant_reply = final.get("report") or ""
    else:
        text = route.get("text") or ""
        if text:
            assistant_reply = text
        else:
            lm_messages = [Message(role=m["role"], content=m["content"]) for m in history]
            lm_messages.append(Message(role="user", content=user_message))
            resp = client.generate(messages=lm_messages, system=system_prompt, max_tokens=3500)
            assistant_reply = resp.text.strip()

    if not assistant_reply:
        return assistant_reply

    save_turn(conversation_id, user_message, assistant_reply)

    try:
        from memory.consolidation import maybe_consolidate
        full = load_history(conversation_id, limit=10000)
        turn_count = len(full) // 2
        first_question = next((m["content"] for m in full if m.get("role") == "user"), "")
        maybe_consolidate(conversation_id, user_id, turn_count, full, first_question, tenant_id)
    except Exception:
        pass

    if route.get("type") == "agent":
        try:
            from memory.chat_context import chroma_store
            chroma_store(conversation_id, user_id, user_message, assistant_reply)
        except Exception:
            pass

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


def _sse_tool(data: dict) -> str:
    return f"event: tool\ndata: {json.dumps({'name': data.get('tool', ''), 'status': data.get('status', 'ok'), 'duration_ms': data.get('duration_ms', 0)})}\n\n"


def _sse_gate(data: dict) -> str:
    return f"event: gate\ndata: {json.dumps({'node': data.get('node', ''), 'route': data.get('route', '')})}\n\n"


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

    LLM-on-top: single llm_route() call at the start of every turn decides
    whether to invoke the agent graph or reply directly — no keyword classify.
    Graph handles cache, clarify interrupt, and all intent dispatch.

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

    from memory.retrieval_gate import should_retrieve
    do_retrieve, refined_query = should_retrieve(user_message)

    episodes: list[dict] = []
    if is_first_turn and do_retrieve:
        try:
            from memory.episodic import retrieve_similar
            episodes = retrieve_similar(refined_query, user_id, top_k=3)
        except Exception:
            episodes = []

    chroma_turns: list[str] = []
    if do_retrieve:
        try:
            from memory.chat_context import chroma_retrieve
            # Run in thread — ChromaDB is synchronous I/O; must not block the event loop
            chroma_turns = await asyncio.to_thread(chroma_retrieve, refined_query, user_id, 3)
        except Exception:
            pass

    system_prompt = _build_system(user_memory, episodes, chroma_turns=chroma_turns)
    client = create_client()

    from agents.state import make_initial_state
    from agents.graph import build_graph
    from agents.checkpointer import PostgresCheckpointer
    from langgraph.types import Command

    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer)
    thread_config = {"configurable": {"thread_id": conversation_id}}

    # ── Clarification resume: graph is waiting for user answer ────────────────
    try:
        prior_state = await asyncio.to_thread(app.get_state, thread_config)
        is_interrupted = bool(prior_state and prior_state.next)
    except Exception:
        is_interrupted = False

    if is_interrupted:
        # Resume graph directly — skip llm_route, clarify_node owns this turn.
        yield _sse_status("routing")
        try:
            task = asyncio.create_task(
                asyncio.to_thread(app.invoke, Command(resume=user_message), thread_config)
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
        # Fall through to graph result handling below.
        intent = final.get("intent", "conversation")
        ticker = final.get("ticker") or None
        yield _sse_status("routing", agent=intent, ticker=ticker)
        report = final.get("report") or ""
        yield _sse_status("streaming", agent=intent)
        for line in report.split("\n"):
            yield _sse_chunk(line + "\n")
        assistant_reply = report
        if assistant_reply:
            try:
                save_turn(conversation_id, user_message, assistant_reply)
            except Exception:
                pass
        yield _sse_done(len(assistant_reply), intent)
        return

    # ── LLM on top: single call decides route OR replies directly ─────────────
    yield _sse_status("routing")
    from agents.conversation_router import llm_route
    route = llm_route(user_message, history, system_prompt, client)

    assistant_reply = ""

    if route.get("type") == "agent":
        # ── Agent path: invoke graph with pre-classified intent/ticker ────────
        intent   = route["intent"]
        ticker   = route.get("ticker", "")
        query    = route.get("query") or user_message

        log.info("llm_route.agent conv=%s intent=%s ticker=%s", conversation_id[:8], intent, ticker)
        yield _sse_status("routing", agent=intent, ticker=ticker or None)

        agent_state = make_initial_state(
            query,
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            messages=history,
        )
        agent_state["intent"] = intent
        agent_state["ticker"] = ticker
        agent_state["original_query"] = user_message

        # Invoke graph in a thread with current_observer set, so synthesize_final's
        # emit_llm_delta flows out as SSE in real time. Falls back to line-chunking
        # (did_stream=False) for intents that don't stream (e.g. market_brief template).
        from tracing import current_observer
        loop = asyncio.get_event_loop()
        stream_q: asyncio.Queue = asyncio.Queue()
        stream_final: dict = {}
        stream_err: dict = {}

        def _observer(kind: str, data: dict) -> None:
            try:
                loop.call_soon_threadsafe(stream_q.put_nowait, (kind, data))
            except RuntimeError:
                pass

        def _run() -> None:
            current_observer.set(_observer)
            try:
                stream_final["v"] = app.invoke(agent_state, thread_config)
            except Exception as exc:
                stream_err["v"] = exc
            finally:
                try:
                    loop.call_soon_threadsafe(stream_q.put_nowait, None)
                except RuntimeError:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        did_stream = False
        stream_status_sent = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(stream_q.get(), timeout=HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item is None:
                    break
                kind, data = item
                if kind == "llm_delta":
                    if not stream_status_sent:
                        yield _sse_status("streaming", agent=intent)
                        stream_status_sent = True
                    did_stream = True
                    yield _sse_chunk(data.get("text", ""))
                elif kind == "tool":
                    yield _sse_tool(data)
                elif kind == "gate":
                    yield _sse_gate(data)
        except asyncio.CancelledError:
            thread.join(timeout=1)
            return
        thread.join(timeout=1)
        if stream_err.get("v"):
            yield f"event: error\ndata: {json.dumps({'error': str(stream_err['v'])})}\n\n"
            return
        final = stream_final.get("v")

        # Check if clarify_node interrupted
        try:
            after_state = await asyncio.to_thread(app.get_state, thread_config)
            if after_state and after_state.next:
                interrupts = after_state.tasks[0].interrupts if after_state.tasks else []
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

        if final.get("_cache_hit"):
            cached = final.get("report", "")
            log.info("cache.hit conv=%s intent=%s ticker=%s tier=%s",
                     conversation_id[:8], intent, ticker, final.get("_cache_tier", ""))
            yield _sse_status("cache_hit", tier=final.get("_cache_tier", ""), ticker=ticker or None)
            for line in cached.split("\n"):
                yield _sse_chunk(line + "\n")
            assistant_reply = cached
        else:
            report = final.get("report") or ""
            if did_stream:
                assistant_reply = report  # already emitted token-by-token via llm_delta
            else:
                yield _sse_status("streaming", agent=intent)
                for line in report.split("\n"):
                    yield _sse_chunk(line + "\n")
                assistant_reply = report

    else:
        # ── Direct reply: tool or free-text response ──────────────────────────
        text = route.get("text") or ""
        log.info("llm_route.text conv=%s len=%d", conversation_id[:8], len(text))
        if not text:
            # LLM returned neither tool call nor text — fallback streaming call
            lm_messages = [Message(role=m["role"], content=m["content"]) for m in history]
            lm_messages.append(Message(role="user", content=user_message))
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
        else:
            yield _sse_status("streaming", agent="conversation")
            for line in text.split("\n"):
                yield _sse_chunk(line + "\n")
            assistant_reply = text

    if not assistant_reply:
        yield _sse_done(0, route.get("intent", "conversation"))
        return

    # ── Persist + extract preferences ─────────────────────────────────────────
    try:
        save_turn(conversation_id, user_message, assistant_reply)

        from memory.consolidation import maybe_consolidate
        full = load_history(conversation_id, limit=10000)
        turn_count = len(full) // 2
        first_question = next((m["content"] for m in full if m.get("role") == "user"), "")
        maybe_consolidate(conversation_id, user_id, turn_count, full, first_question, tenant_id)

        if route.get("type") == "agent":
            try:
                from memory.chat_context import chroma_store
                await asyncio.to_thread(chroma_store, conversation_id, user_id, user_message, assistant_reply)
            except Exception:
                pass

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

    yield _sse_done(len(assistant_reply), route.get("intent", "conversation"))


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
