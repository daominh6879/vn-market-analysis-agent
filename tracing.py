"""
tracing.py — Observability helpers.

Public API (unchanged):
  instrument_tool(name=None)   — decorator for tool functions
  instrument_llm(client)       — wraps LLMClient.generate()
  current_request_id           — ContextVar[str] set per turn

New:
  Tracer                       — event sink with .event(kind, data)
  get_tracer()                 — singleton Tracer
  compose(*observers)          — fan-out multiple Observer callables
  Observer                     — Callable[[str, dict], None]
  current_turn_start_ts        — ContextVar[float] set in turn_start for latency calc
  current_observer             — ContextVar[Observer | None] fanned out on every event
                                 (used by dashboard SSE streaming)
  _redact()                    — strips API keys/tokens from error strings

File layout:
  traces/YYYY-MM-DD.jsonl      — daily trace (all events, never rotated)
  traces/latest.jsonl          — rolling 2000-line window of same events
  traces/usage.jsonl           — permanent LLM cost ledger (never rotated)
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ── Public types ──────────────────────────────────────────────────────────────
Observer = Callable[[str, dict], None]

# ── ContextVars ───────────────────────────────────────────────────────────────
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_request_id", default=""
)
current_turn_start_ts: contextvars.ContextVar[float] = contextvars.ContextVar(
    "current_turn_start_ts", default=0.0
)
current_observer: contextvars.ContextVar[Observer | None] = contextvars.ContextVar(
    "current_observer", default=None
)

# ── Paths ─────────────────────────────────────────────────────────────────────
_TRACES_DIR = Path(__file__).parent / "traces"
_LATEST_FILE = _TRACES_DIR / "latest.jsonl"
_USAGE_FILE = _TRACES_DIR / "usage.jsonl"
_MAX_LATEST_LINES = 2000


# ── Write helpers ─────────────────────────────────────────────────────────────

def _dated_file() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _TRACES_DIR / f"{date_str}.jsonl"


def _write_trace(entry: dict) -> None:
    try:
        _TRACES_DIR.mkdir(exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with _dated_file().open("a", encoding="utf-8") as f:
            f.write(line)
        with _LATEST_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
        lines = _LATEST_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LATEST_LINES:
            _LATEST_FILE.write_text(
                "\n".join(lines[-_MAX_LATEST_LINES:]) + "\n", encoding="utf-8"
            )
    except Exception:
        pass


def _write_usage(entry: dict) -> None:
    try:
        _TRACES_DIR.mkdir(exist_ok=True)
        with _USAGE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# ── Secret redaction ──────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{20,})", re.I),           # OpenAI/DeepSeek keys
    re.compile(r"(Bearer\s+[A-Za-z0-9\-._~+/]{20,})", re.I),
    re.compile(r"([A-Za-z0-9]{32,})", re.I),              # generic long tokens — error context only
]


def _redact(s: str) -> str:
    if not s:
        return s
    for p in _SECRET_PATTERNS[:2]:  # only first two — avoid over-redacting previews
        s = p.sub("[REDACTED]", s)
    return s


# ── OpenTelemetry (optional — activates when OTEL_EXPORTER_OTLP_ENDPOINT set) ──

_otel_tracer = None
_otel_provider = None
_otel_root_span: contextvars.ContextVar = contextvars.ContextVar("otel_root_span", default=None)

_SPAN_NAMES = {"llm": "llm_call", "tool": "tool_call", "gate": "gate"}


def _ensure_otel():
    global _otel_tracer, _otel_provider
    if _otel_tracer is not None:
        return _otel_tracer
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        _otel_tracer = False
        return False
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        otel_trace.set_tracer_provider(provider)
        _otel_provider = provider
        _otel_tracer = otel_trace.get_tracer("ai-engineer")
    except Exception:
        _otel_tracer = False
    return _otel_tracer


def _span_attrs(kind: str, data: dict) -> dict:
    if kind == "llm":
        return {
            "llm.model": str(data.get("model", "")),
            "llm.status": str(data.get("status", "")),
            "llm.tokens_in": int(data.get("tokens_in", 0) or 0),
            "llm.tokens_out": int(data.get("tokens_out", 0) or 0),
        }
    if kind == "tool":
        return {
            "tool.name": str(data.get("tool", "")),
            "tool.status": str(data.get("status", "")),
        }
    if kind == "gate":
        return {
            "gate.node": str(data.get("node", "")),
            "gate.route": str(data.get("route", "")),
        }
    return {}


def _otel_child(kind: str, data: dict) -> None:
    t = _ensure_otel()
    if not t:
        return
    try:
        from opentelemetry import trace as otel_trace
        root = _otel_root_span.get(None)
        ctx = otel_trace.set_span_in_context(root) if root is not None else None
        name = _SPAN_NAMES.get(kind, kind)
        with t.start_as_current_span(name, attributes=_span_attrs(kind, data), context=ctx):
            pass
    except Exception:
        pass


def _otel_start_root(attrs: dict) -> None:
    t = _ensure_otel()
    if not t:
        return
    try:
        span = t.start_span("agent_run", attributes={f"agent.{k}": str(v) for k, v in attrs.items()})
        _otel_root_span.set(span)
    except Exception:
        pass


def _otel_end_root() -> None:
    span = _otel_root_span.get(None)
    if span is None:
        return
    try:
        span.end()
        _otel_root_span.set(None)
        if _otel_provider is not None:
            _otel_provider.force_flush()
    except Exception:
        pass


# ── Event funnel — single sink for trace write + observer fan-out + OTel span ──

def _emit(kind: str, data: dict) -> None:
    data = dict(data)
    if data.get("error"):
        data["error"] = _redact(str(data["error"]))
    _write_trace({
        "type": kind,
        "ts": time.time(),
        "request_id": current_request_id.get(""),
        **data,
    })
    obs = current_observer.get(None)
    if obs is not None:
        try:
            obs(kind, data)
        except Exception:
            pass
    _otel_child(kind, data)


def emit_llm_delta(text: str) -> None:
    """Push a token-level text delta to current_observer (kind="llm_delta").

    No trace file write — token spam stays out of traces/latest.jsonl.
    Used by streamed synthesize nodes so the SSE client sees text as it generates.
    """
    if not text:
        return
    obs = current_observer.get(None)
    if obs is None:
        return
    try:
        obs("llm_delta", {"text": text})
    except Exception:
        pass


# ── Repr helpers ──────────────────────────────────────────────────────────────

def _safe_repr(value, max_len: int = 200) -> str:
    try:
        import pandas as pd
        if isinstance(value, pd.DataFrame):
            return f"DataFrame({len(value)} rows × {len(value.columns)} cols)"
    except ImportError:
        pass
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = repr(value)
    return s[:max_len] + "…" if len(s) > max_len else s


def _result_summary(result) -> dict:
    try:
        status = getattr(result, "status", None)
        message = getattr(result, "message", None)
        data = getattr(result, "data", None)
        return {
            "status": status or "ok",
            "preview": (message or "")[:300] if message else _safe_repr(data, 300),
        }
    except Exception:
        return {"status": "ok", "preview": _safe_repr(result, 300)}


# ── Tracer class ──────────────────────────────────────────────────────────────

class Tracer:
    """Event sink. Call .event() directly or pass .event as an Observer."""

    def event(self, kind: str, data: dict) -> None:
        _emit(kind, data)

    def turn_start(self, query: str, intent: str = "", ticker: str = "") -> None:
        t0 = time.time()
        current_turn_start_ts.set(t0)
        _otel_start_root({"query": query[:300], "intent": intent, "ticker": ticker})
        self.event("turn_start", {"query": query[:300], "intent": intent, "ticker": ticker})

    def turn_end(self, intent: str = "", ticker: str = "",
                 report_len: int = 0, cache_hit: bool = False) -> None:
        t0 = current_turn_start_ts.get(0.0)
        latency_ms = round((time.time() - t0) * 1000) if t0 else 0
        self.event("turn_end", {
            "intent": intent,
            "ticker": ticker,
            "latency_ms": latency_ms,
            "report_len": report_len,
            "cache_hit": cache_hit,
        })
        _otel_end_root()


_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer


# ── Observer utilities ────────────────────────────────────────────────────────

def compose(*observers: Observer) -> Observer:
    """Fan-out: call each observer with the same (kind, data)."""
    def _combined(kind: str, data: dict) -> None:
        for obs in observers:
            try:
                obs(kind, data)
            except Exception:
                pass
    return _combined


# ── LLM wrapper ───────────────────────────────────────────────────────────────

def _default_model_name() -> str:
    """Resolve the active model name from env (stream() has no resp.model to read)."""
    return (
        os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("GEMINI_MODEL")
        or os.environ.get("OLLAMA_CLOUD_MODEL")
        or os.environ.get("OLLAMA_MODEL")
        or ""
    )


def instrument_llm(client):
    """Wrap any LLMClient to log generate() + stream() calls to trace + usage ledger."""
    from llm.base import LLMClient

    provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()

    class _FileLLMWrapper(LLMClient):
        def __init__(self, inner: LLMClient):
            self._inner = inner

        def generate(self, messages, *, model=None, max_tokens=1024,
                     system=None, tools=None, temperature=None):
            rid = current_request_id.get("")
            n_msgs = len(messages) if messages else 0
            t0 = time.perf_counter()
            try:
                resp = self._inner.generate(
                    messages, model=model, max_tokens=max_tokens,
                    system=system, tools=tools, temperature=temperature,
                )
                dur = round((time.perf_counter() - t0) * 1000)
                # Use the requested/env model name, NOT resp.model: DeepSeek echoes an
                # internal alias ("deepseek-v4-flash") that isn't in the pricing table.
                used_model = model or _default_model_name()
                try:
                    from llm.pricing import estimate_cost
                    cost = estimate_cost(provider, used_model, resp.input_tokens, resp.output_tokens)
                except Exception:
                    cost = 0.0
                _emit("llm", {
                    "tool": "llm.generate",
                    "model": used_model,
                    "args": {"messages": n_msgs, "model": used_model, "max_tokens": max_tokens},
                    "status": "ok",
                    "preview": (resp.text or "")[:300],
                    "duration_ms": dur,
                    "tokens_in": resp.input_tokens,
                    "tokens_out": resp.output_tokens,
                    "cost_usd": cost,
                    "error": None,
                })
                _write_usage({
                    "ts": time.time(),
                    "request_id": rid,
                    "provider": provider,
                    "model": used_model,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cost_usd": cost,
                })
                return resp
            except Exception as exc:
                dur = round((time.perf_counter() - t0) * 1000)
                _emit("llm", {
                    "tool": "llm.generate",
                    "model": model or _default_model_name(),
                    "args": {"messages": n_msgs, "model": model or _default_model_name(), "max_tokens": max_tokens},
                    "status": "error",
                    "preview": str(exc)[:300],
                    "duration_ms": dur,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                    "error": str(exc),
                })
                raise

        def stream(self, messages, *, model=None, max_tokens=1024, system=None):
            rid = current_request_id.get("")
            n_msgs = len(messages) if messages else 0
            used_model = model or _default_model_name()
            t0 = time.perf_counter()
            parts: list[str] = []
            try:
                for chunk in self._inner.stream(
                    messages, model=model, max_tokens=max_tokens, system=system
                ):
                    parts.append(chunk)
                    yield chunk
                dur = round((time.perf_counter() - t0) * 1000)
                full_text = "".join(parts)
                from llm.pricing import estimate_cost, estimate_tokens
                in_tokens = estimate_tokens(system or "") + sum(
                    estimate_tokens(m.content) for m in messages
                )
                out_tokens = estimate_tokens(full_text)
                cost = estimate_cost(provider, used_model, in_tokens, out_tokens)
                _emit("llm", {
                    "tool": "llm.stream",
                    "model": used_model,
                    "args": {"messages": n_msgs, "model": used_model, "max_tokens": max_tokens},
                    "status": "ok",
                    "preview": full_text[:300],
                    "duration_ms": dur,
                    "tokens_in": in_tokens,
                    "tokens_out": out_tokens,
                    "cost_usd": cost,
                    "error": None,
                })
                _write_usage({
                    "ts": time.time(),
                    "request_id": rid,
                    "provider": provider,
                    "model": used_model,
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens,
                    "cost_usd": cost,
                })
            except Exception as exc:
                dur = round((time.perf_counter() - t0) * 1000)
                _emit("llm", {
                    "tool": "llm.stream",
                    "model": used_model,
                    "args": {"messages": n_msgs, "model": used_model, "max_tokens": max_tokens},
                    "status": "error",
                    "preview": str(exc)[:300],
                    "duration_ms": dur,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                    "error": str(exc),
                })
                raise

    return _FileLLMWrapper(client)


# ── Tool decorator ────────────────────────────────────────────────────────────

def instrument_tool(name: str | None = None):
    """Decorator: log every tool call to trace files + optional Langfuse span."""
    def decorator(fn):
        tool_name = name or fn.__name__

        langfuse_fn = fn
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            try:
                from langfuse import observe
                langfuse_fn = observe(name=tool_name, as_type="tool")(fn)
            except Exception:
                langfuse_fn = fn

        def logged_fn(*args, **kwargs):
            rid = current_request_id.get("")
            try:
                import inspect
                sig = inspect.signature(fn)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                log_args = {k: _safe_repr(v) for k, v in bound.arguments.items()}
            except Exception:
                log_args = {}

            t0 = time.perf_counter()
            try:
                result = langfuse_fn(*args, **kwargs)
                duration_ms = round((time.perf_counter() - t0) * 1000)
                summary = _result_summary(result)
                _emit("tool", {
                    "tool": tool_name,
                    "args": log_args,
                    "status": summary["status"],
                    "preview": summary["preview"],
                    "duration_ms": duration_ms,
                    "error": None,
                })
                return result
            except Exception as exc:
                duration_ms = round((time.perf_counter() - t0) * 1000)
                _emit("tool", {
                    "tool": tool_name,
                    "args": log_args,
                    "status": "error",
                    "preview": str(exc)[:300],
                    "duration_ms": duration_ms,
                    "error": str(exc),
                })
                raise

        logged_fn.__name__ = fn.__name__
        logged_fn.__doc__ = fn.__doc__
        return logged_fn

    return decorator
