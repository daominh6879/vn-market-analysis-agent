"""
tools/registry.py — Metadata cho mọi tool (bài 20).

version:     phiên bản tool (tăng khi thay đổi interface)
timeout:     thời gian chờ tối đa (giây) trước khi coi là upstream_error
cost_hint:   "free" (pure compute) | "low" (external API, rẻ) | "medium" (LLM call)
side_effect: True nếu tool thay đổi trạng thái bên ngoài (ghi DB, gửi order)
"""

from __future__ import annotations

TOOL_REGISTRY: dict[str, dict] = {
    "get_realtime_price": {
        "version": "2.0",
        "timeout": 10,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_realtime_price_intl": {
        "version": "2.0",
        "timeout": 10,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_historical_ohlcv": {
        "version": "2.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_historical_ohlcv_intl": {
        "version": "2.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "calculate_indicators": {
        "version": "2.0",
        "timeout": 5,
        "cost_hint": "free",
        "side_effect": False,
    },
    "search_financial_news": {
        "version": "2.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "analyze_market_sentiment": {
        "version": "2.0",
        "timeout": 30,
        "cost_hint": "medium",
        "side_effect": False,
    },
    "get_market_performance": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_market_breadth": {
        "version": "1.0",
        "timeout": 20,
        "cost_hint": "low",
        "side_effect": False,
    },
    # ── Phase 3: world / commodity / crypto / FX / VN gold ──────────────────
    "get_global_indices": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_commodities": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_crypto_prices": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_fx_rates": {
        "version": "1.0",
        "timeout": 10,
        "cost_hint": "low",
        "side_effect": False,
    },
    "get_vn_gold": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
    # ── Phase 1: HOSE universe + top movers ─────────────────────────────────
    "get_top_movers": {
        "version": "1.0",
        "timeout": 15,
        "cost_hint": "low",
        "side_effect": False,
    },
}


def get_meta(name: str) -> dict:
    """Trả metadata của tool. Raises KeyError nếu không tìm thấy."""
    if name not in TOOL_REGISTRY:
        raise KeyError(f"Tool '{name}' chưa được đăng ký trong registry.")
    return TOOL_REGISTRY[name]
