"""
tests/test_tool_cache.py — Tool-level cache tests (Bài 32B).

Unit tests for tools/cache.py — no LLM / Redis / network.

Run:
    pytest tests/test_tool_cache.py -v
"""

from __future__ import annotations

from tools.cache import clear, tool_cache_get, tool_cache_set
from tools.result import ToolResult


def _args(ticker="HPG", **kw):
    return {"ticker": ticker, **kw}


# ── Basic roundtrip ────────────────────────────────────────────────────────────

def test_set_then_get_returns_same_object():
    clear()
    tool_cache_set("get_realtime_price", _args("HPG"), ToolResult(
        status="ok", data=123.0, message="Giá HPG"))
    hit = tool_cache_get("get_realtime_price", _args("HPG"))
    assert hit is not None
    assert hit.status == "ok"
    assert hit.data == 123.0


def test_different_args_miss():
    clear()
    tool_cache_set("get_realtime_price", _args("HPG"), ToolResult(
        status="ok", data=1.0, message=""))
    assert tool_cache_get("get_realtime_price", _args("VCB")) is None


def test_provider_arg_excluded_from_key():
    """Swapping provider (test mock) must not change cache identity."""
    clear()
    tool_cache_set("get_historical_ohlcv", _args("HPG", days=60), ToolResult(
        status="ok", data="df", message=""))
    hit = tool_cache_get("get_historical_ohlcv", _args("HPG", days=60, provider="MOCK"))
    assert hit is not None
    assert hit.data == "df"


# ── Status gating ──────────────────────────────────────────────────────────────

def test_error_status_not_cached():
    clear()
    tool_cache_set("get_realtime_price", _args("HPG"), ToolResult(
        status="upstream_error", data=None, message="boom"))
    assert tool_cache_get("get_realtime_price", _args("HPG")) is None


def test_no_data_is_cached():
    clear()
    tool_cache_set("search_financial_news", _args("HPG", days=7), ToolResult(
        status="no_data", data=None, message="không có tin"))
    hit = tool_cache_get("search_financial_news", _args("HPG", days=7))
    assert hit is not None and hit.status == "no_data"


# ── Metadata gating ────────────────────────────────────────────────────────────

def test_free_tool_not_cached():
    """Pure-compute tools (cost_hint=free) must never cache."""
    clear()
    tool_cache_set("calculate_indicators", {"df": object()}, ToolResult(
        status="ok", data="RSI", message=""))
    assert tool_cache_get("calculate_indicators", {"df": object()}) is None


def test_unregistered_tool_not_cached():
    clear()
    tool_cache_set("no_such_tool", _args(), ToolResult(status="ok", data=1, message=""))
    assert tool_cache_get("no_such_tool", _args()) is None


def test_zero_arg_tool_not_cached():
    """Live-snapshot tools (get_crypto_prices etc.) have no distinguishing input."""
    clear()
    tool_cache_set("get_crypto_prices", {}, ToolResult(status="ok", data={}, message=""))
    assert tool_cache_get("get_crypto_prices", {}) is None


# ── TTL policy ─────────────────────────────────────────────────────────────────

def test_ttl_override_per_tool():
    """Per-tool TTL differs from cost_hint default (price 15s < low default 30s)."""
    from tools.cache import _ttl_for
    assert _ttl_for("get_realtime_price", "low") == 15
    assert _ttl_for("analyze_market_sentiment", "medium") == 600
    assert _ttl_for("get_foreign_flows", "low") == 60


def test_ttl_falls_back_to_cost_hint():
    from tools.cache import _ttl_for
    assert _ttl_for("get_market_breadth", "low") == 60  # overridden
    assert _ttl_for("some_low_tool", "low") == 30       # fallback


# ── Integration with instrument_tool ──────────────────────────────────────────

def test_instrument_tool_short_circuits_second_call():
    """Decorated fn runs once; second call returns cached ToolResult."""
    from tracing import instrument_tool

    clear()
    calls = {"n": 0}

    @instrument_tool("get_realtime_price")
    def fake(ticker: str):
        calls["n"] += 1
        return ToolResult(status="ok", data=999.0, message=f"price {ticker}")

    r1 = fake("ZZZCACHE")
    r2 = fake("ZZZCACHE")
    assert calls["n"] == 1, "second identical call must hit cache, not re-run"
    assert r1.data == r2.data == 999.0
    clear()
