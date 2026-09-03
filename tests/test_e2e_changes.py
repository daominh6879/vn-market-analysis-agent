"""
tests/test_e2e_changes.py — End-to-end tests for the observability + streaming changes.

Covers the code touched in this change set (all hit real DeepSeek LLM + real tools/DB):

  LLM / streaming
    - synthesize_final streams tokens via client.stream() + emit_llm_delta
    - stream_turn surfaces `event: tool` / `event: gate` SSE lines via current_observer
  tracing.py
    - turn_start / turn_end / tool events written to traces/latest.jsonl
    - usage.jsonl cost ledger written (llm/pricing.py estimate_cost)
  memory
    - memory/retrieval_gate.should_retrieve (real LLM)
    - memory/consolidation.consolidate (real LLM)
  tools (real DB)
    - tools/price.get_sector_performance  (week/month periods)
    - tools/ohlcv_db.query_vn30_latest     (sessions param)
    - tools/price.get_historical_ohlcv     (DB-first path)

Run (needs DeepSeek key + Postgres running):
    python -m pytest tests/test_e2e_changes.py -v -s -m e2e

Run only the no-network unit tests:
    python -m pytest tests/test_e2e_changes.py -v -k "unit"
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

# App debug prints (e.g. decompose_node) emit Vietnamese text. On Windows the
# default console is cp1252, so `print` inside graph nodes raises UnicodeEncodeError
# and aborts the turn. Reconfigure to UTF-8 so real app output doesn't crash here.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# ── SSE helpers ────────────────────────────────────────────────────────────────


def _parse_sse(lines: list[str]) -> dict[str, list[dict]]:
    """Parse SSE blocks into {event: [data_dict, ...]}.

    Each `lines` element is one SSE block (stream_turn yields whole blocks ending
    in ``\n\n``). A bare `data:` block (no `event:` field) is a "message" event, so
    the event name must reset per block — a preceding `event: status` must not
    swallow the following text chunks.
    """
    events: dict[str, list[dict]] = {}
    for raw in lines:
        event = "message"
        payloads: list[dict] = []
        for s in raw.split("\n"):
            s = s.strip()
            if not s:
                continue
            if s.startswith("event: "):
                event = s[len("event: "):].strip()
            elif s.startswith("data: "):
                try:
                    payloads.append(json.loads(s[len("data: "):].strip()))
                except Exception:
                    continue
        for p in payloads:
            events.setdefault(event, []).append(p)
    return events


def _run_stream(cid: str, user_id: str, message: str, is_first_turn: bool = False) -> list[str]:
    from memory.turn_handler import stream_turn

    lines: list[str] = []

    async def _run():
        async for line in stream_turn(
            conversation_id=cid,
            user_id=user_id,
            user_message=message,
            tenant_id="default",
            is_first_turn=is_first_turn,
        ):
            lines.append(line)

    asyncio.run(_run())
    return lines


@pytest.fixture
def conversation_id():
    from memory.conversation import create_conversation

    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(user_id, "default")
    return cid, user_id


# ── E2E: streaming + tracing through the full turn ─────────────────────────────

@pytest.mark.e2e
def test_stream_turn_emits_tool_gate_and_traces(conversation_id):
    """One full agent turn must: stream text, emit tool+gate SSE, write trace + usage."""
    cid, user_id = conversation_id
    traces_dir = ROOT / "traces"
    latest = traces_dir / "latest.jsonl"
    usage = traces_dir / "usage.jsonl"

    start_ts = time.time()
    before_usage = len(usage.read_text(encoding="utf-8").splitlines()) if usage.exists() else 0

    # Use macro_sector (a _ALWAYS_SCOPE_INTENTS intent): its cache key hashes the
    # full question, so the unique suffix guarantees a cache MISS → full graph path
    # (decompose → tools → streaming synthesize). technical_analysis would be keyed
    # by (intent, ticker) only and hit the cache, skipping tool/gate events.
    query = f"Tình hình ngành ngân hàng hiện nay {uuid.uuid4().hex[:8]}"
    lines = _run_stream(cid, user_id, query, is_first_turn=True)
    events = _parse_sse(lines)

    # 1. done event with saved=True
    assert events.get("done"), "Expected event: done at end of stream"
    assert events["done"][0].get("saved") is True

    # 2. text streamed (token chunks via llm_delta, or line-chunk fallback)
    text = "".join(p.get("text", "") for p in events.get("message", []))
    assert text.strip(), "Expected streamed report text"
    print(f"\n  [stream] {len(events.get('message', []))} chunks, {len(text)} chars")
    print(f"  [report] {text[:160]!a}")

    # 3. tool + gate events surfaced via current_observer
    tool_names = [p.get("name") for p in events.get("tool", [])]
    gate_nodes = [p.get("node") for p in events.get("gate", [])]
    assert tool_names, "Expected at least one `event: tool` SSE line (gather/tool call)"
    assert gate_nodes, "Expected at least one `event: gate` SSE line (routing)"
    print(f"  [tools] {tool_names}")
    print(f"  [gates] {gate_nodes}")

    # 4. tracing: turn_start / turn_end / tool written to latest.jsonl.
    # latest.jsonl rotates at 2000 lines, so don't assert line count grows — filter
    # by timestamp to isolate events written during THIS turn.
    new_events = []
    for line in latest.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("ts", 0) >= start_ts - 2:
            new_events.append(ev)
    types = {e.get("type") for e in new_events}
    assert "turn_start" in types, f"Missing turn_start in trace: {types}"
    assert "turn_end" in types, f"Missing turn_end in trace: {types}"
    assert "tool" in types, f"Missing tool event in trace: {types}"
    print(f"  [trace] this turn wrote {len(new_events)} events: {sorted(types)}")

    # 5. usage.jsonl cost ledger grew (classify/decompose/route use generate())
    after_usage = usage.read_text(encoding="utf-8").splitlines()
    assert len(after_usage) > before_usage, "usage.jsonl must grow after an agent turn"
    last_usage = json.loads(after_usage[-1])
    assert "cost_usd" in last_usage and "model" in last_usage
    print(f"  [usage] last: {last_usage.get('model')} ${last_usage.get('cost_usd', 0):.6f}")


# ── E2E: memory retrieval gate (real LLM) ──────────────────────────────────────

@pytest.mark.e2e
def test_retrieval_gate_skips_greeting(monkeypatch):
    """RETRIEVAL_GATE=1 → greeting should be classified retrieve=false."""
    monkeypatch.setenv("RETRIEVAL_GATE", "1")
    from memory.retrieval_gate import should_retrieve

    do_retrieve, refined = should_retrieve("Xin chào, bạn khỏe không?")
    assert do_retrieve is False, f"Greeting should skip retrieval, got retrieve=True"
    print(f"\n  [gate] greeting → retrieve={do_retrieve}")


@pytest.mark.e2e
def test_retrieval_gate_retrieves_followup(monkeypatch):
    """RETRIEVAL_GATE=1 → follow-up question should be classified retrieve=true."""
    monkeypatch.setenv("RETRIEVAL_GATE", "1")
    from memory.retrieval_gate import should_retrieve

    do_retrieve, refined = should_retrieve("Như tôi đã hỏi hôm qua, HPG đang thế nào rồi?")
    assert do_retrieve is True, "Follow-up should retrieve history"
    assert refined, "Refined query should be non-empty"
    print(f"\n  [gate] follow-up → retrieve={do_retrieve} refined={refined!a}")


@pytest.mark.e2e
def test_retrieval_gate_off_by_default(monkeypatch):
    """Flag unset → returns (True, query) without calling LLM (fail-open pass-through)."""
    monkeypatch.delenv("RETRIEVAL_GATE", raising=False)
    from memory.retrieval_gate import should_retrieve

    do_retrieve, refined = should_retrieve("Xin chào!")
    assert do_retrieve is True
    assert refined == "Xin chào!"


# ── E2E: memory consolidation (real LLM) ───────────────────────────────────────

@pytest.mark.e2e
def test_consolidate_extracts_facts():
    """consolidate() must parse the small model's JSON into facts + episode."""
    from memory.consolidation import consolidate, ConsolidationResult

    turns = [
        {"role": "user", "content": "Tôi đang theo dõi cổ phiếu HPG và VCB."},
        {"role": "assistant", "content": "Đã ghi nhận. Bạn muốn phân tích gì về HPG và VCB?"},
        {"role": "user", "content": "So sánh biên lợi nhuận hai mã này giúp tôi."},
        {"role": "assistant", "content": "HPG biên gộp ~11%, VCB NIM ~3%."},
    ]
    result = consolidate(turns)
    assert result is not None, "consolidate() returned None (LLM JSON parse failed)"
    assert isinstance(result, ConsolidationResult)
    assert isinstance(result.facts, list), f"facts must be a list, got {type(result.facts)}"
    assert isinstance(result.episode, str)
    print(f"\n  [consolidate] facts={result.facts!a} episode={result.episode[:80]!a}")


# ── E2E: tools (real DB) ───────────────────────────────────────────────────────

@pytest.mark.e2e
def test_sector_performance_week_month():
    """get_sector_performance must support week (5 sessions) and month (22 sessions)."""
    from tools.price import get_sector_performance

    for period in ("day", "week", "month"):
        r = get_sector_performance(period)
        assert r.status == "ok", f"{period}: status={r.status}, message={r.message}"
        assert r.data, f"{period}: expected non-empty sector list"
        assert isinstance(r.data, list) and isinstance(r.data[0], dict)
        assert "pct_change" in r.data[0] or "weighted_pct" in r.data[0], \
            f"{period}: sector row missing pct field: {r.data[0].keys()}"
        print(f"\n  [sector/{period}] {len(r.data)} sectors, top={r.data[0]!a}")


@pytest.mark.e2e
def test_sector_performance_invalid_period():
    from tools.price import get_sector_performance

    r = get_sector_performance("year")
    assert r.status == "invalid_input"
    assert "week" in r.message or "month" in r.message


@pytest.mark.e2e
def test_query_vn30_latest_sessions():
    """query_vn30_latest must return close vs close `sessions` back, with pct_change."""
    from tools.ohlcv_db import query_vn30_latest

    df = query_vn30_latest(["HPG", "VCB", "FPT"], sessions=5)
    assert df is not None and not df.empty, "Expected OHLCV rows from ohlcv_daily"
    assert set(df.columns) >= {"ticker", "date", "close", "prev_close", "pct_change"}, \
        f"Missing columns: {df.columns.tolist()}"
    assert len(df["ticker"].unique()) >= 1
    assert (df["pct_change"].notna()).any()
    print(f"\n  [vn30/5] {len(df)} rows, tickers={sorted(df['ticker'].unique())}")


@pytest.mark.e2e
def test_historical_ohlcv_db_first():
    """get_historical_ohlcv must prefer the DB (ohlcv_daily) and return ok on success."""
    from tools.price import get_historical_ohlcv

    r = get_historical_ohlcv("HPG", days=30)
    assert r.status == "ok", f"status={r.status}, message={r.message}"
    assert r.data is not None and len(r.data) >= 2
    print(f"\n  [ohlcv/HPG] {len(r.data)} sessions, source message: {r.message[:80]!a}")


# ── Unit (no network): pricing + freshness + consolidation gating ──────────────

def test_unit_pricing_estimate_cost():
    from llm.pricing import estimate_cost

    # deepseek-chat: $0.27 in / $1.10 out per 1M
    assert estimate_cost("deepseek", "deepseek-chat", 1_000_000, 0) == pytest.approx(0.27)
    assert estimate_cost("deepseek", "deepseek-chat", 0, 1_000_000) == pytest.approx(1.10)
    # prefix match on full model id
    assert estimate_cost("anthropic", "claude-opus-5-20251001", 1_000_000, 0) == pytest.approx(15.00)
    # ollama free
    assert estimate_cost("ollama", "llama3", 1_000_000, 1_000_000) == 0.0
    # unknown model → fallback $1 in / $3 out
    assert estimate_cost("deepseek", "unknown-model", 1_000_000, 1_000_000) == pytest.approx(4.0)


def test_unit_previous_trading_day_skips_weekend():
    from tools.price import _previous_trading_day

    # Monday → Friday (skip Sat/Sun)
    mon = date(2026, 9, 7)
    assert mon.weekday() == 0  # guard: is Monday
    assert _previous_trading_day(mon) == mon - timedelta(days=3)
    # mid-week → previous weekday
    wed = date(2026, 9, 9)  # Wednesday, not a VN holiday
    assert wed.weekday() == 2  # guard: is Wednesday
    assert _previous_trading_day(wed) == wed - timedelta(days=1)
    # holiday mid-week → skip holiday, land on previous trading day
    assert _previous_trading_day(date(2026, 9, 2)) == date(2026, 8, 31)  # 2/9 holiday
    # always a weekday
    assert _previous_trading_day(date(2026, 9, 6)).weekday() < 5  # Sunday input


def test_unit_is_db_fresh():
    from tools.price import _is_db_fresh, _previous_trading_day

    ref = date(2026, 9, 7)  # Monday
    prev = _previous_trading_day(ref)  # Friday
    assert _is_db_fresh(prev, ref.isoformat()) is True          # db == prev trading day
    assert _is_db_fresh(prev - timedelta(days=1), ref.isoformat()) is False  # stale
    assert _is_db_fresh(None, ref.isoformat()) is False
    assert _is_db_fresh("not-a-date", ref.isoformat()) is False


def test_unit_maybe_consolidate_gating(monkeypatch):
    import memory.consolidation as c

    calls: list[int] = []
    monkeypatch.setattr(c, "CONSOLIDATE_EVERY", 3)
    monkeypatch.setattr(
        c, "consolidate",
        lambda turns: (calls.append(len(turns)) or c.ConsolidationResult(["fact"], "episode")),
    )
    # stub persistence so no DB writes happen
    try:
        import memory.reader as reader
        monkeypatch.setattr(reader, "save_memory_item", lambda **kw: None)
    except Exception:
        pass
    try:
        import memory.episodic as episodic
        monkeypatch.setattr(episodic, "store_episode", lambda **kw: None)
    except Exception:
        pass

    for i in range(1, 8):
        c.maybe_consolidate("cid", "uid", turn_count=i, turns=[{"role": "user", "content": "hi"}])

    assert len(calls) == 2, f"Expected consolidate on turns 3 and 6, got {len(calls)} calls"
