"""Unit tests for the fixes driven by vn_stock_agent_test_report.md (no DB, no LLM)."""

import pandas as pd
import pytest

from agents.guardrails import disclaimer_for, scrub_internal
from agents.intents.fundamentals import _rank_text
from agents.intents.price_action import _session_count, _session_table


# ── guardrails (detectors moved to the router LLM — see tests/test_llm_routing_contract.py) ──

def test_disclaimer_texts():
    assert "không ai có thể dự đoán" in disclaimer_for({"prediction"}, "vi").lower()
    assert disclaimer_for(set()) == ""


def test_scrub_internal_names():
    out = scrub_internal("Không có trong financial_facts/stock_prices [KHÔNG CÓ TEMPLATE SQL]")
    assert "financial_facts" not in out and "stock_prices" not in out and "TEMPLATE" not in out


# ── P/E wording (report #3, #5) ───────────────────────────────────────────────

def test_rank_text_wording_by_value():
    peers = [("MWG", 15.9), ("DGW", 18.9), ("FRT", 28.2), ("PNJ", 3.5), ("VRE", 8.2)]
    text = _rank_text(15.9, peers, "P/E", higher_is_better=False, subject="MWG")
    assert "thấp hơn DGW(18.9)" in text and "FRT(28.2)" in text
    assert "cao hơn" in text and "PNJ(3.5)" in text and "VRE(8.2)" in text
    assert text.index("thấp hơn") < text.index("cao hơn")


# ── price_action sessions ─────────────────────────────────────────────────────

def test_session_count():
    assert _session_count("5 phiên VIC") == 5
    assert _session_count("10 phiên ACB") == 10
    assert _session_count("FPT hôm qua vs hôm nay") == 2
    assert _session_count("999 phiên") == 30
    assert _session_count("giá FPT") is None


def test_session_table_changes():
    df = pd.DataFrame({
        "time": pd.date_range("2026-10-01", periods=4),
        "close": [100.0, 110.0, 99.0, 99.0],
        "volume": [1000, 2000, 3000, 4000],
    })
    table = _session_table(df, 3, is_index=False)
    assert "+10.00%" in table and "-10.00%" in table and "+0.00%" in table
    assert table.count("\n| 2026") == 3
