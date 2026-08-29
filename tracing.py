"""
tracing.py — Langfuse instrumentation helpers.

instrument_tool(name=None) — decorates a sync tool function as a Langfuse tool span.
  - No-op (returns original function unchanged) when LANGFUSE keys not in env.
  - Evaluated at decoration time (module import) — zero runtime overhead when disabled.

Usage:
    from tracing import instrument_tool

    @instrument_tool("get_realtime_price")
    def get_realtime_price(ticker: str) -> ToolResult: ...
"""

from __future__ import annotations

import os


def instrument_tool(name: str | None = None):
    """Wrap a sync function as a Langfuse tool span. No-op if Langfuse not configured."""
    def decorator(fn):
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return fn
        try:
            from langfuse import observe
            return observe(name=name or fn.__name__, as_type="tool")(fn)
        except Exception:
            return fn
    return decorator
