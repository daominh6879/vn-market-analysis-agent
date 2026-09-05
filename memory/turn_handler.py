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
import os
import threading
import time
from datetime import datetime, timezone
from typing import AsyncIterator

log = logging.getLogger(__name__)

INTERRUPT_STALE_SECONDS = float(os.environ.get("INTERRUPT_STALE_SECONDS", "600"))


def _snapshot_ts(snap) -> datetime | None:
    """Extract the checkpoint timestamp from a LangGraph StateSnapshot.

    Prefers the `created_at` datetime; falls back to the raw checkpoint `ts` ISO string
    (LangGraph >=0.2 keeps it in `checkpoint["ts"]`). Naive datetimes are assumed UTC.
    """
    ts = getattr(snap, "created_at", None)
    if ts is not None:
        # LangGraph >=0.3 returns created_at as an ISO string, not a datetime.
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return None
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    cp = getattr(snap, "checkpoint", None)
    raw = cp.get("ts") if isinstance(cp, dict) else None
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _build_focus(values: dict):
    """Build a Focus from a checkpoint's state values.

    Reconstructs the full ticker list from `state["tickers"]` (plural) plus
    `extract_focus_entities(state["original_query"])`, so a comparison turn's 2nd/3rd
    ticker survives even though the graph's top-level `state["ticker"]` only ever stored
    one. Returns None when the checkpoint has no dialogue state worth carrying forward.
    """
    from agents.focus import Focus, extract_focus_entities, extract_focus_sector

    intent = values.get("intent", "")
    tickers: list[str] = list(values.get("tickers") or [])
    single = values.get("ticker", "")
    if not tickers and single:
        tickers = [single]
    for t in extract_focus_entities(values.get("original_query", "")):
        if t not in tickers:
            tickers.append(t)
    sector = values.get("sector", "")
    if not sector and not tickers:
        # Sector-only focus (macro_sector/market_brief): derive the subject from the prior
        # self-contained query when the checkpoint predates sector tracking.
        sector = extract_focus_sector(values.get("query", ""))
    if not intent and not tickers and not sector:
        return None
    return Focus(
        tickers=tickers,
        sector=sector,
        intent=intent,
        query=values.get("query") or values.get("original_query", ""),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _read_prior(app, thread_config: dict) -> dict:
    """Read last-turn focus + interrupt status from the checkpointer.

    Returns {interrupted, stale, focus}. `stale` is True when the graph is interrupted
    but the interrupt is older than INTERRUPT_STALE_SECONDS (user abandoned the
    clarification) — such an interrupt must be dropped, not resumed.
    """
    empty = {"interrupted": False, "stale": False, "focus": None}
    try:
        snap = app.get_state(thread_config)
    except Exception:
        return empty
    if not snap:
        return empty
    values = getattr(snap, "values", {}) or {}
    interrupted = bool(getattr(snap, "next", None))
    stale = False
    if interrupted:
        ts = _snapshot_ts(snap)
        if ts is not None:
            stale = (datetime.now(timezone.utc) - ts).total_seconds() > INTERRUPT_STALE_SECONDS
    return {
        "interrupted": interrupted,
        "stale": stale,
        "focus": _build_focus(values),
    }

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
    user_id: str = "",
    tenant_id: str = "default",
) -> str:
    parts = [_BASE_SYSTEM]

    typed_keys: tuple = ()
    if user_id:
        # Typed long-term profile (doc §6.2) — machine-readable fields, rendered structured
        # so the LLM sees them as a profile, not a generic key/value blob.
        try:
            from memory.reader import TYPED_KEYS, load_typed_preferences
            typed_keys = TYPED_KEYS
            typed = load_typed_preferences(user_id, tenant_id)
            typed_lines = []
            if typed.get("preferred_market"):
                typed_lines.append(f"- preferred_market: {typed['preferred_market']}")
            if typed.get("favorite_tickers"):
                typed_lines.append(f"- favorite_tickers: {', '.join(typed['favorite_tickers'])}")
            if typed.get("preferred_analysis"):
                typed_lines.append(f"- preferred_analysis: {typed['preferred_analysis']}")
            if typed_lines:
                parts.append("\nHồ sơ người dùng (đã gõ):\n" + "\n".join(typed_lines))
        except Exception:
            typed_keys = ()

    if user_memory:
        memory_lines = "\n".join(
            f"- {m['key']}: {m['value']} (confidence={m['confidence']:.2f})"
            for m in user_memory
            if m.get("key") not in typed_keys
        )
        if memory_lines:
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


def _build_agent_state(
    route: dict,
    user_message: str,
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    history: list[dict],
):
    """Build an AgentState from a route (or a mixed agent segment).

    Shared by run_turn / stream_turn so the agent path stays consistent — including
    time_context, which run_turn previously dropped (harmless, but now unified).
    """
    from agents.state import make_initial_state

    query = route.get("query") or user_message
    state = make_initial_state(
        query,
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        messages=history,
    )
    state["intent"] = route.get("intent", "")
    state["ticker"] = route.get("ticker", "")
    state["tickers"] = route.get("tickers") or ([state["ticker"]] if state["ticker"] else [])
    state["sector"] = route.get("sector", "")
    state["original_query"] = user_message
    state["time_context"] = route.get("time_context")
    return state


def _invoke_agent(app, state, thread_config) -> str:
    """Invoke the agent graph synchronously and return the report text ('' if none)."""
    final = app.invoke(state, thread_config)
    return final.get("report") or ""


def _mixed_parts(
    app,
    segments,
    user_message: str,
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    history: list[dict],
    thread_config: dict,
) -> list[str]:
    """Execute a mixed route's segments, returning ordered reply parts.

    agent segments run the graph; general/out_of_scope segments are pre-drafted text. The
    first agent segment runs on the main thread (preserves focus carry-forward); any extra
    agent segment (rare) runs on an isolated thread so a clarify interrupt can't swallow it.
    """
    from agents.classifier import OUT_OF_SCOPE_REPLY

    parts: list[str] = []
    agent_i = 0
    for seg in segments or []:
        kind = seg.get("kind", "")
        if kind == "agent":
            cfg = thread_config
            if agent_i > 0:
                base_tid = thread_config.get("configurable", {}).get("thread_id", "conv")
                cfg = {"configurable": {"thread_id": f"{base_tid}:mix{agent_i}"}}
            agent_i += 1
            state = _build_agent_state(seg, user_message, conversation_id, user_id, tenant_id, history)
            report = _invoke_agent(app, state, cfg)
            if report:
                parts.append(report)
        elif kind == "out_of_scope":
            parts.append(seg.get("text") or OUT_OF_SCOPE_REPLY)
        elif kind == "general":
            text = (seg.get("text") or "").strip()
            if text:
                parts.append(text)
    return parts


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

    system_prompt = _build_system(user_memory, episodes, chroma_turns=chroma_turns, user_id=user_id, tenant_id=tenant_id)
    client = create_client()

    from agents.conversation_router import llm_route
    from agents.graph import build_graph
    from agents.checkpointer import PostgresCheckpointer

    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer)
    thread_config = {"configurable": {"thread_id": conversation_id}}

    # Prior-turn focus + stuck-interrupt detection.
    prior = _read_prior(app, thread_config)
    if prior["stale"]:
        # Abandon the stuck clarify interrupt: fresh thread orphans it so this turn
        # routes + runs normally instead of being swallowed by the old interrupt.
        thread_config = {"configurable": {"thread_id": f"{conversation_id}:stale:{int(time.time())}"}}

    route = llm_route(
        user_message, history, system_prompt, client,
        focus=prior["focus"],
    )

    if route.get("type") == "agent":
        agent_state = _build_agent_state(route, user_message, conversation_id, user_id, tenant_id, history)
        assistant_reply = _invoke_agent(app, agent_state, thread_config)
    elif route.get("type") == "mixed":
        parts = _mixed_parts(app, route.get("segments"), user_message, conversation_id, user_id, tenant_id, history, thread_config)
        assistant_reply = "\n\n".join(p for p in parts if p)
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

    system_prompt = _build_system(user_memory, episodes, chroma_turns=chroma_turns, user_id=user_id, tenant_id=tenant_id)
    client = create_client()

    from agents.graph import build_graph
    from agents.checkpointer import PostgresCheckpointer
    from langgraph.types import Command

    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer)
    thread_config = {"configurable": {"thread_id": conversation_id}}

    # ── Clarification resume: graph is waiting for user answer ────────────────
    prior = await asyncio.to_thread(_read_prior, app, thread_config)
    if prior["stale"]:
        # Abandon the stuck clarify interrupt (older than INTERRUPT_STALE_SECONDS):
        # a fresh thread orphans it, and this turn routes + runs normally instead
        # of being swallowed by the old interrupt.
        thread_config = {"configurable": {"thread_id": f"{conversation_id}:stale:{int(time.time())}"}}
    is_interrupted = prior["interrupted"] and not prior["stale"]

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
    route = llm_route(
        user_message, history, system_prompt, client,
        focus=prior["focus"],
    )

    assistant_reply = ""

    if route.get("type") == "agent":
        # ── Agent path: invoke graph with pre-classified intent/ticker ────────
        intent   = route["intent"]
        ticker   = route.get("ticker", "")

        log.info("llm_route.agent conv=%s intent=%s ticker=%s", conversation_id[:8], intent, ticker)
        yield _sse_status("routing", agent=intent, ticker=ticker or None)

        agent_state = _build_agent_state(route, user_message, conversation_id, user_id, tenant_id, history)

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

        report = final.get("report") or ""
        if did_stream:
            assistant_reply = report  # already emitted token-by-token via llm_delta
        else:
            yield _sse_status("streaming", agent=intent)
            for line in report.split("\n"):
                yield _sse_chunk(line + "\n")
            assistant_reply = report

    elif route.get("type") == "mixed":
        parts = _mixed_parts(app, route.get("segments"), user_message, conversation_id, user_id, tenant_id, history, thread_config)
        assistant_reply = "\n\n".join(p for p in parts if p)
        yield _sse_status("streaming", agent="mixed")
        for line in assistant_reply.split("\n"):
            yield _sse_chunk(line + "\n")

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
