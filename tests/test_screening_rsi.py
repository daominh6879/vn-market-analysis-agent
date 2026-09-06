"""
tests/test_screening_rsi.py — Unit tests for RSI screening (agents/intents/screening.py).

Pure parsing only — no LLM, no DB, no network. Run with:
    pytest tests/test_screening_rsi.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.intents.screening import (
    _parse_rsi_filter,
    _normalize_filters,
    _cmp,
    _matches_all,
    _FUNDAMENTAL_COLS,
    _FACTS_METRICS,
    _TECHNICAL_FNS,
)


def test_rsi_dưới_threshold():
    assert _parse_rsi_filter("Lọc RSI dưới 30 ngành thép") == ("<", 30)


def test_rsi_trên_threshold():
    assert _parse_rsi_filter("cổ phiếu RSI trên 70") == (">", 70)


def test_rsi_symbol_operator():
    assert _parse_rsi_filter("RSI < 40") == ("<", 40)
    assert _parse_rsi_filter("RSI > 60") == (">", 60)


def test_rsi_no_direction_defaults_below():
    assert _parse_rsi_filter("RSI 30") == ("<", 30)


def test_not_rsi_returns_none():
    assert _parse_rsi_filter("top 5 mã ROE cao nhất") is None
    assert _parse_rsi_filter("giá cổ phiếu HPG hôm nay") is None


# ── Filter normalization / comparison / matching ─────────────────────────────

def test_normalize_filters_none_and_single_and_list():
    assert _normalize_filters(None) == []
    f = {"indicator": "pe", "op": "<", "threshold": 10}
    assert _normalize_filters(f) == [f]
    assert _normalize_filters([f]) == [f]
    assert _normalize_filters([f, None, "junk"]) == [f]  # non-dicts dropped


def test_cmp_all_operators():
    assert _cmp(5, "<", 10)
    assert _cmp(10, "<=", 10)
    assert _cmp(15, ">", 10)
    assert _cmp(10, ">=", 10)
    assert not _cmp(15, "<", 10)
    assert not _cmp(5, ">", 10)


def test_matches_all_and():
    filters = [
        {"indicator": "pe", "op": "<", "threshold": 10},
        {"indicator": "roe", "op": ">", "threshold": 20},
    ]
    assert _matches_all({"pe": 8, "roe": 25}, filters)
    assert not _matches_all({"pe": 12, "roe": 25}, filters)   # pe fails
    assert not _matches_all({"pe": 8, "roe": 10}, filters)    # roe fails
    assert not _matches_all({"pe": 8}, filters)               # roe missing → fail
    assert not _matches_all({"pe": None, "roe": 25}, filters)  # NaN → fail


# ── Indicator registry coverage ───────────────────────────────────────────────

def test_fundamental_cols_cover_key_indicators():
    for ind in ("pe", "pb", "roe", "roa", "eps", "ev_ebitda", "de",
                "gross_margin", "net_margin", "revenue_growth", "earnings_growth"):
        assert ind in _FUNDAMENTAL_COLS, f"missing fundamental indicator {ind}"


def test_technical_fns_cover_key_indicators():
    for ind in ("rsi", "macd", "volume", "adx",
                "ma20", "ma50", "ma200", "pct_52w_high", "pct_52w_low"):
        assert ind in _TECHNICAL_FNS, f"missing technical indicator {ind}"


def test_facts_metrics_cover_revenue_profit():
    for ind in ("revenue", "profit"):
        assert ind in _FACTS_METRICS, f"missing financial_facts indicator {ind}"
