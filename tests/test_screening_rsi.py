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
    _normalize_filters,
    _cmp,
    _matches_all,
    _FUNDAMENTAL_COLS,
    _FACTS_METRICS,
    _TECHNICAL_FNS,
)


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


def test_untraded_ticker_is_skipped_by_technical_screen(monkeypatch):
    """BCG case: 30 sessions of zero volume + flat price gave RSI 0.00 and topped 'RSI < 30'."""
    import pandas as pd
    import agents.intents.screening as sc
    n = 40
    halted = pd.DataFrame({"close": [3.0] * 20 + [2.5] * 20, "high": [3.0] * n, "low": [2.5] * n,
                           "volume": [1000.0] * 20 + [0.0] * 20})
    assert sc._is_untraded(halted)
    monkeypatch.setattr(sc, "_load_ohlcv_bulk", lambda tickers, end: {"BCG": halted})
    assert sc._screen_technical(["BCG"], [{"indicator": "rsi", "op": "<", "threshold": 30}], None) == []
