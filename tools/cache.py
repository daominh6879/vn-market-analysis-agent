"""
tools/cache.py — In-memory TTL cache for tool results (bài 32B).

Caches ToolResult at the tool-function boundary (hooked into `instrument_tool`).
Single tier: SHA-256(tool_name + version + args) → in-memory dict with expiry.

Policy (driven by TOOL_REGISTRY metadata):
- side_effect=True      → never cached (writes external state)
- cost_hint="free"      → never cached (pure compute, nothing to save)
- cost_hint="low"       → cached 30s  (external API, avoid re-fetch in one agent run)
- cost_hint="medium"    → cached 300s (LLM call, most expensive)
- Per-tool override      → `_TTL_OVERRIDES` (e.g. price 15s, sentiment 600s)
- Only `ok` / `no_data` results cached — errors (upstream_error, rate_limited,
  invalid_input) are transient and never cached.

`provider` kwarg excluded from key — test/mock injection must not change identity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── TTL ────────────────────────────────────────────────────────────────────────

# Default TTL by cost_hint — fallback when a tool has no explicit override.
_TTL_BY_COST: dict[str, int] = {
    "low": 30,       # external API — fresh enough within a single agent run
    "medium": 300,   # LLM call — avoid re-LLM same ticker/query
}
_DEFAULT_TTL = 30

# Per-tool TTL overrides (seconds) — faster/slower than the cost_hint default.
_TTL_OVERRIDES: dict[str, int] = {
    "get_realtime_price":      15,   # price ticks fast
    "get_realtime_price_intl": 15,
    "get_historical_ohlcv":    300,  # daily bars — stable intraday
    "get_historical_ohlcv_intl": 300,
    "search_financial_news":   120,  # news refreshes slower than price
    "analyze_market_sentiment": 600, # LLM call — most expensive, cache longest
    "get_market_performance":  60,
    "get_market_breadth":      60,
    "get_top_movers":          60,
    "get_foreign_flows":       60,
    "get_sector_performance":  120,
}


def _ttl_for(tool_name: str, cost_hint: str) -> int:
    return _TTL_OVERRIDES.get(tool_name, _TTL_BY_COST.get(cost_hint, _DEFAULT_TTL))

# Only cache these terminal statuses — never transient errors.
_CACHEABLE_STATUSES = frozenset({"ok", "no_data"})

_store: dict[str, tuple[Any, float]] = {}


def _meta(tool_name: str) -> Optional[dict]:
    from tools.registry import get_meta
    try:
        return get_meta(tool_name)
    except KeyError:
        return None  # unregistered tool → not cacheable


def _cache_key(tool_name: str, args: dict) -> Optional[str]:
    meta = _meta(tool_name)
    if meta is None:
        return None
    if meta.get("side_effect"):
        return None
    if meta.get("cost_hint") == "free":
        return None
    # Exclude provider — swapping providers (test mock) must not change cache identity.
    key_args = {k: v for k, v in args.items() if k != "provider"}
    # Zero-arg tools (get_crypto_prices, get_fx_rates, get_vn_gold, …) have no
    # distinguishing input — a name-only key freezes a live snapshot across calls.
    if not key_args:
        return None
    try:
        payload = json.dumps(
            {"tool": tool_name, "version": meta.get("version"), "args": key_args},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    except Exception:
        return None
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Cache operations ──────────────────────────────────────────────────────────

def tool_cache_get(tool_name: str, args: dict) -> Optional[Any]:
    """Return cached ToolResult, or None on miss / not-cacheable."""
    key = _cache_key(tool_name, args)
    if key is None:
        return None
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        del _store[key]
        return None
    log.debug("tool_cache.hit tool=%s", tool_name)
    return value


def tool_cache_set(tool_name: str, args: dict, result: Any) -> None:
    """Store a ToolResult if its status is cacheable."""
    status = getattr(result, "status", None)
    if status not in _CACHEABLE_STATUSES:
        return
    meta = _meta(tool_name)
    if meta is None:
        return
    key = _cache_key(tool_name, args)
    if key is None:
        return
    ttl = _ttl_for(tool_name, meta.get("cost_hint"))
    _store[key] = (result, time.monotonic() + ttl)
    log.debug("tool_cache.set tool=%s ttl=%ds", tool_name, ttl)


def clear() -> None:
    """Drop all cached entries (tests, cache-bust)."""
    _store.clear()
