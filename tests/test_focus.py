"""
tests/test_focus.py — pure unit tests for the dialogue-state focus layer (agents/focus.py).

No LLM / network / DB — `resolve_focus` and `extract_focus_entities` are deterministic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

from agents.focus import (
    AMBIGUOUS,
    CONTINUE,
    FRESH,
    Focus,
    extract_focus_entities,
    extract_focus_sector,
    resolve_focus,
)


def _focus(tickers=(), intent="", sector="", query=""):
    return Focus(tickers=list(tickers), intent=intent, sector=sector, query=query)


# ── extract_focus_entities ─────────────────────────────────────────────────────

def test_extract_focus_entities_wraps_core(monkeypatch):
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG", "VCB", "BID", "CTG"])
    assert extract_focus_entities("phân tích HPG và VCB") == ["HPG", "VCB"]
    assert extract_focus_entities("so sánh BID và CTG") == ["BID", "CTG"]
    assert extract_focus_entities("thị trường hôm nay thế nào") == []
    assert extract_focus_entities("") == []


def test_extract_focus_sector():
    assert extract_focus_sector("phân tích ngành ngân hàng") == "ngân hàng"
    assert extract_focus_sector("giá thép hôm nay") == "thép"
    assert extract_focus_sector("dầu thô brent tăng") == "năng lượng"
    assert extract_focus_sector("VNINDEX hôm nay") == "VNINDEX"
    assert extract_focus_sector("VN30 tuần này") == "VN30"
    assert extract_focus_sector("tỷ giá USD/VND") == ""  # FX, not a sector keyword
    assert extract_focus_sector("phân tích HPG") == ""    # ticker, not sector


# ── resolve_focus ──────────────────────────────────────────────────────────────

def test_new_ticker_is_fresh():
    """New ticker present → FRESH, tickers = new tickers, no carry-forward."""
    prior = _focus(tickers=["VCB"], intent="valuation")
    kind, entities = resolve_focus("phân tích HPG", prior)
    assert kind == FRESH
    assert entities == ["HPG"]


def test_continuation_carries_prior_tickers_unchanged():
    """No ticker + continuation phrase → carry forward prior tickers unchanged."""
    prior = _focus(tickers=["BID", "CTG"], intent="valuation")
    kind, entities = resolve_focus("phân tích sâu hơn", prior)
    assert kind == CONTINUE
    assert entities == ["BID", "CTG"]


def test_no_signal_is_ambiguous():
    """No ticker, no continuation phrase → AMBIGUOUS (let LLM/clarify decide)."""
    prior = _focus(tickers=["VCB"], intent="valuation")
    kind, entities = resolve_focus("cảm ơn bạn", prior)
    assert kind == AMBIGUOUS
    assert entities == []


def test_continuation_phrase_and_new_ticker_is_fresh():
    """Continuation phrase AND new ticker in same message → FRESH (the bug being fixed)."""
    prior = _focus(tickers=["VCB"], intent="valuation")
    kind, entities = resolve_focus("phân tích thêm HPG", prior)
    assert kind == FRESH
    assert entities == ["HPG"]


def test_continuation_without_prior_is_ambiguous():
    """Continuation phrase with no prior focus (first turn) → AMBIGUOUS."""
    kind, entities = resolve_focus("phân tích sâu hơn", None)
    assert kind == AMBIGUOUS
    assert entities == []


def test_continuation_sector_only_focus_is_continue():
    """Continuation after a sector/market focus (no tickers) still inherits intent."""
    prior = _focus(tickers=[], intent="macro_sector", sector="ngân hàng")
    kind, entities = resolve_focus("phân tích sâu hơn", prior)
    assert kind == CONTINUE
    assert entities == []


def test_fresh_with_no_prior():
    """First turn naming a ticker → FRESH (no prior to carry)."""
    kind, entities = resolve_focus("giá HPG hôm nay", None)
    assert kind == FRESH
    assert entities == ["HPG"]
