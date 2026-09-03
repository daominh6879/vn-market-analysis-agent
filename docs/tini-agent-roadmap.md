# Tini-Agent Feature Roadmap

Remaining features to port from `tini-agent` into `ai-engineer`.
Ordered by priority. Each item includes exact files, changes, and acceptance criteria.

---

## 1. OpenTelemetry Spans (arize-phoenix)

**Goal:** When `OTEL_EXPORTER_OTLP_ENDPOINT` is set, emit a parent span `agent_run` per turn with child spans for every `llm` and `tool` event. Viewable locally via arize-phoenix at `http://localhost:6006`.

**Files to change:**
- `tracing.py` — add `OtelTracer` class alongside existing `Tracer`
- `requirements.txt` — add optional group `[tracing]`

**New dependency (optional install):**
```
pip install 'arize-phoenix[evals]' opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

**Implementation:**

`tracing.py` — extend `Tracer.event()`:
```python
# in __init__ or module-level init:
_otel_enabled = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
if _otel_enabled:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    _provider = TracerProvider()
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    _otel_tracer = trace.get_tracer("ai-engineer")
```

`Tracer` class changes:
- `turn_start()` → opens root span `agent_run`, stores in `current_span` ContextVar
- `turn_end()` → ends root span, flushes `BatchSpanProcessor`
- `event("llm", ...)` → starts+ends child span `llm_call` with token attrs
- `event("tool", ...)` → starts+ends child span `tool_call` with tool name + status attrs
- `event("gate", ...)` → starts+ends child span `gate` with node/route attrs

`requirements.txt` addition:
```
# optional: pip install ai-engineer[tracing]
arize-phoenix>=4.0.0
opentelemetry-sdk>=1.20.0
opentelemetry-exporter-otlp-proto-grpc>=1.20.0
```

**`.env` flag:**
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317   # arize-phoenix default
```

**Start phoenix locally:**
```bash
python -m phoenix.server.main serve
# UI → http://localhost:6006
```

**Acceptance:** A turn with 2 LLM calls + 3 tool calls produces 1 `agent_run` span with 5 child spans visible in Phoenix UI.

---

## 2. Streaming Chat in Dashboard

**Goal:** Add a chat panel to the dashboard so users can send queries and see streaming token output + tool events without leaving the browser.

**Files to change:**
- `ops/dashboard.py` — add `/api/chat/stream` SSE route + Chat tab HTML/JS

**New route `GET /api/chat/stream?q=<query>`:**
- Spawns `agents.graph.build_graph()` in a thread
- Streams SSE lines: `data: {"type":"token","delta":"..."}`, `data: {"type":"tool","name":"...","status":"ok"}`, `data: {"type":"done","report_len":N}`
- Uses `compose(tracer.event, sse_observer)` so tracing + streaming happen simultaneously

**Handler addition in `_Handler.do_GET`:**
```python
elif self.path.startswith("/api/chat/stream"):
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
    self._stream_chat(q)
```

**`_stream_chat(query)` method:**
```python
import queue, threading
from agents.graph import build_graph
from agents.state import make_initial_state

self.send_response(200)
self.send_header("Content-Type", "text/event-stream")
self.send_header("Cache-Control", "no-cache")
self.send_header("Access-Control-Allow-Origin", "*")
self.end_headers()

q: queue.Queue = queue.Queue()

def observer(kind, data):
    q.put((kind, data))

def run():
    graph = build_graph()
    state = make_initial_state(query=query)
    graph.invoke(state)  # all events flow through observer via compose()
    q.put(None)  # sentinel

threading.Thread(target=run, daemon=True).start()
while True:
    item = q.get()
    if item is None:
        break
    kind, data = item
    line = f"data: {json.dumps({'type': kind, **data})}\n\n"
    self.wfile.write(line.encode())
    self.wfile.flush()
```

**Dashboard HTML addition — Chat tab:**
- Text input + Send button
- Scrolling output div showing streamed tokens + tool badges in real-time
- SSE via `EventSource('/api/chat/stream?q=' + encodeURIComponent(query))`

**Note:** Requires wiring `observer` into `classify_node` and LLM client. Need to pass observer via ContextVar `current_observer: ContextVar[Observer | None]` so `instrument_llm` and `instrument_tool` can fan-out to it.

**Acceptance:** Type a query in dashboard Chat tab, see tokens stream live, tool chips appear as tools run.

---

## 3. Eval Tab in Dashboard

**Goal:** Show RAGAS eval results in the dashboard — last run scores + history trend.

**Files to change:**
- `ops/dashboard.py` — add `collect_evals()` + Evals tab

**`collect_evals()` function:**
```python
def collect_evals() -> dict:
    results_files = [
        _ROOT / "evals" / "results.json",
        _ROOT / "evals" / "arch_compare.json",
        _ROOT / "evals" / "router_eval.json",
        _ROOT / "evals" / "rag_fusion_eval.json",
        _ROOT / "evals" / "reranker_results.json",
    ]
    reports = {}
    for f in results_files:
        if f.exists():
            try:
                reports[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return reports
```

**New route `GET /api/evals`** → returns `collect_evals()`.

**Evals tab HTML** — for each report file:
- Table: metric name → score (color-coded: green ≥ 0.8, yellow 0.6–0.8, red < 0.6)
- Last-modified timestamp from file stat
- Collapsible per report section

**Acceptance:** After running `python evals/run.py`, dashboard Evals tab shows faithfulness/relevancy/etc scores with color coding.

---

## 4. Daily Usage Aggregation

**Goal:** `usage.jsonl` grouped by date + model, shown as a trend table in the Usage tab.

**Files to change:**
- `ops/dashboard.py` — extend `collect()` + update Usage tab HTML

**`collect()` addition — daily breakdown:**
```python
from collections import defaultdict
daily: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
for u in usage_raw:
    ts = u.get("ts", 0)
    day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
    key = f"{day}/{u.get('model','?')}"
    daily[key]["calls"] += 1
    daily[key]["input_tokens"] += u.get("input_tokens", 0)
    daily[key]["output_tokens"] += u.get("output_tokens", 0)
    daily[key]["cost_usd"] += u.get("cost_usd", 0.0)

usage_daily = [
    {"day_model": k, **v, "cost_usd": round(v["cost_usd"], 4)}
    for k, v in sorted(daily.items(), reverse=True)
]
```

**Usage tab** — two sub-tabs: **By Model** (existing) + **By Day** (new table with `day_model`, calls, tokens, cost).

**Acceptance:** Usage tab shows a daily cost breakdown table. Running two queries on different days creates two rows.

---

## 5. Memory Retrieval Gate

**Goal:** Before querying `memory/episodic.py` or `memory/conversation.py`, run a small fast model call that decides `{"retrieve": true/false, "query": "..."}`. Skips memory lookup for irrelevant turns (greetings, simple price queries).

**Files to change:**
- `memory/retrieval_gate.py` — NEW FILE
- `memory/turn_handler.py` — wire gate before memory read

**`memory/retrieval_gate.py`:**
```python
"""
retrieval_gate.py — Small-model gate that decides whether memory retrieval is needed.
Fails open (returns retrieve=True) on any error.
"""
from __future__ import annotations
import json

_SYSTEM = """Decide if retrieving conversation history helps answer this query.
Reply ONLY with JSON: {"retrieve": true/false, "query": "refined search query if retrieve=true", "reason": "one line"}
retrieve=false for: greetings, simple real-time price checks, market breadth queries.
retrieve=true for: follow-up questions, personalization needed, context-dependent queries."""

def should_retrieve(query: str) -> tuple[bool, str]:
    """Returns (should_retrieve, refined_query). Fails open."""
    try:
        from llm.factory import create_client
        from llm.types import Message
        client = create_client()
        resp = client.generate(
            [Message(role="user", content=f"Query: {query}")],
            system=_SYSTEM,
            max_tokens=128,
            temperature=0.0,
        )
        data = json.loads(resp.text.strip())
        return bool(data.get("retrieve", True)), data.get("query", query)
    except Exception:
        return True, query  # fail open
```

**`memory/turn_handler.py` change** — wrap memory read:
```python
from memory.retrieval_gate import should_retrieve

def _load_context(query: str, conversation_id: str) -> str:
    do_retrieve, refined = should_retrieve(query)
    if not do_retrieve:
        return ""
    # existing memory read logic with refined query
    ...
```

**`.env` flag (opt-out):**
```
RETRIEVAL_GATE=1   # default off; set to 1 to enable
```

**Acceptance:** A query like "giá HPG" skips memory retrieval (gate returns false). A query like "so sánh với lần trước" triggers retrieval (gate returns true).

---

## 6. Memory Consolidation

**Goal:** Every N turns (default 6), run a small model over recent `chat_log` rows to extract durable facts and an episode summary. Persist to `memory/episodic.py` and `memory/extractor.py` stores.

**Files to change:**
- `memory/consolidation.py` — NEW FILE
- `memory/turn_handler.py` — call `maybe_consolidate()` after each turn

**`memory/consolidation.py`:**
```python
"""
consolidation.py — Extract facts + episode from recent turns via small model.
Runs every CONSOLIDATE_EVERY turns (default 6).
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass

CONSOLIDATE_EVERY = int(os.getenv("CONSOLIDATE_EVERY", "6"))

_SYSTEM = """Read this conversation excerpt and extract:
1. facts[]: durable user preferences or facts worth remembering (e.g. "user tracks HPG and VCB")
2. episode: one-sentence summary of what was discussed today

Reply ONLY with JSON: {"facts": ["...", ...], "episode": "..."}
If nothing worth remembering, return {"facts": [], "episode": ""}"""

@dataclass
class ConsolidationResult:
    facts: list[str]
    episode: str

def consolidate(turns: list[dict]) -> ConsolidationResult | None:
    """Extract facts + episode from a list of {role, content} turns."""
    if not turns:
        return None
    try:
        from llm.factory import create_client
        from llm.types import Message
        client = create_client()
        excerpt = "\n".join(
            f"{t['role'].upper()}: {t['content'][:200]}"
            for t in turns[-12:]  # last 12 messages
        )
        resp = client.generate(
            [Message(role="user", content=excerpt)],
            system=_SYSTEM,
            max_tokens=512,
            temperature=0.0,
        )
        data = json.loads(resp.text.strip())
        return ConsolidationResult(
            facts=data.get("facts", []),
            episode=data.get("episode", ""),
        )
    except Exception:
        return None

def maybe_consolidate(conversation_id: str, turn_count: int, turns: list[dict]) -> None:
    """Call after each turn. Runs consolidation every CONSOLIDATE_EVERY turns."""
    if turn_count % CONSOLIDATE_EVERY != 0:
        return
    result = consolidate(turns)
    if not result:
        return
    # Persist facts
    try:
        from memory.extractor import save_facts
        for fact in result.facts:
            save_facts(conversation_id, [fact])
    except Exception:
        pass
    # Persist episode
    try:
        from memory.episodic import save_episode
        if result.episode:
            save_episode(conversation_id, result.episode)
    except Exception:
        pass
```

**`memory/turn_handler.py` change** — at end of turn:
```python
from memory.consolidation import maybe_consolidate
# after saving exchange to chat_log:
maybe_consolidate(conversation_id, turn_count, recent_turns)
```

**`.env` flag:**
```
CONSOLIDATE_EVERY=6   # run consolidation every N turns
```

**Acceptance:** After 6 turns discussing HPG, `memory/episodic.py` has a new episode row. Facts like "user follows HPG" appear in semantic memory.

---

## 7. Secret Redaction in Error Logs

**Goal:** Strip API keys and tokens from error strings before writing to `traces/*.jsonl`.

**Files to change:**
- `tracing.py` — add `_redact()` helper, apply to all `error` and `preview` fields

**`_redact()` function:**
```python
import re
_SECRET_PATTERNS = [
    re.compile(r'(sk-[A-Za-z0-9]{20,})', re.I),          # OpenAI/DeepSeek keys
    re.compile(r'(Bearer\s+[A-Za-z0-9\-._~+/]{20,})', re.I),
    re.compile(r'([A-Za-z0-9]{32,})', re.I),              # generic long tokens — only in error context
]

def _redact(s: str) -> str:
    if not s:
        return s
    for p in _SECRET_PATTERNS[:2]:  # only first two — avoid over-redacting previews
        s = p.sub(r'[REDACTED]', s)
    return s
```

Apply in `_write_trace()` — redact `error` field only (not preview, which is model output).

**Acceptance:** An error containing `sk-abc123...` in the trace file shows `[REDACTED]` instead.

---

## Implementation Order

```
Week 1:  Items 3 (Eval tab) + 4 (Daily usage) — small, high impact on dashboard
Week 2:  Item 7 (Secret redaction) — tiny, security hygiene
Week 3:  Item 1 (OTel/Phoenix) — medium effort, unlocks Phoenix UI
Week 4:  Item 2 (Streaming chat) — medium effort, requires observer ContextVar wiring
Week 5:  Items 5+6 (Retrieval gate + Consolidation) — touch memory layer together
```

---

## Notes

- Items 3+4 have zero new dependencies.
- Item 1 (OTel) is optional — only activates when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- Items 5+6 need careful testing: consolidation runs a real LLM call; gate adds latency per turn.
- Item 2 (streaming chat) requires wiring a `current_observer` ContextVar through `instrument_llm` and `instrument_tool` — coordinate with `tracing.py` changes.
