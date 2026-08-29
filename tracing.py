"""
tracing.py — Observability helpers.

instrument_tool(name=None)
  - Always logs to traces/latest.jsonl (name, args, result, duration_ms, request_id).
  - Also wraps as Langfuse tool span when LANGFUSE keys present.

request_id contextvar — set by _dispatch_intent in turn_handler, propagated via
asyncio.to_thread() so all nested tool calls share the same request_id.
"""

from __future__ import annotations

import contextvars
import json
import os
import time
from pathlib import Path

# Current request id — set in _dispatch_intent, read by instrument_tool wrappers.
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_request_id", default=""
)

_TRACE_FILE = Path(__file__).parent / "traces" / "latest.jsonl"
_MAX_LINES = 2000  # rotate after this many entries


def _write_trace(entry: dict) -> None:
    try:
        _TRACE_FILE.parent.mkdir(exist_ok=True)
        with _TRACE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Rotate: keep last _MAX_LINES lines
        lines = _TRACE_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LINES:
            _TRACE_FILE.write_text(
                "\n".join(lines[-_MAX_LINES:]) + "\n", encoding="utf-8"
            )
    except Exception:
        pass


def _safe_repr(value, max_len: int = 200) -> str:
    """Best-effort short repr of any value for logging."""
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
    """Extract status + short preview from a ToolResult (or any value)."""
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


def instrument_tool(name: str | None = None):
    """
    Decorator: log every call to traces/latest.jsonl + optional Langfuse span.
    Always active (file logging). Langfuse span only when keys configured.
    """
    def decorator(fn):
        tool_name = name or fn.__name__

        # Build Langfuse wrapper if configured (wraps fn, not logged_fn)
        langfuse_fn = fn
        if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            try:
                from langfuse import observe
                langfuse_fn = observe(name=tool_name, as_type="tool")(fn)
            except Exception:
                langfuse_fn = fn

        def logged_fn(*args, **kwargs):
            rid = current_request_id.get("")
            # Capture args for logging (skip large DataFrames in positional args)
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
                _write_trace({
                    "ts": time.time(),
                    "request_id": rid,
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
                _write_trace({
                    "ts": time.time(),
                    "request_id": rid,
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
