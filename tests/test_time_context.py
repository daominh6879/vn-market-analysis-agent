"""
tests/test_time_context.py — unit tests for core/time_context.py.

No LLM/network: the deterministic fallback (`_fallback`) and helpers are tested directly;
the LLM path is tested with a mocked client.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from core.time_context import (
    TimeContext,
    _fallback,
    resolve_time_context,
    to_days,
)

TODAY = date(2026, 9, 5)
ISO = TODAY.isoformat()


def _days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# ── deterministic fallback ─────────────────────────────────────────────────────

def test_fallback_no_date_defaults_now():
    tc = _fallback("HPG giá bao nhiêu", TODAY)
    assert tc.anchor == ISO
    assert tc.start_date is None
    assert tc.end_date == ISO
    assert tc.explicit is False


def test_fallback_now_words():
    for q in ("giá HPG hôm nay", "giá HPG hiện tại", "giá HPG gần nhất", "HPG today"):
        tc = _fallback(q, TODAY)
        assert tc.explicit is False
        assert tc.start_date is None


def test_fallback_relative_vn():
    assert _fallback("tin HPG tuần trước", TODAY).start_date == _days_ago(7)
    assert _fallback("tin HPG tháng trước", TODAY).start_date == _days_ago(30)
    assert _fallback("tin HPG hôm qua", TODAY).start_date == _days_ago(1)
    assert _fallback("tin HPG quý trước", TODAY).start_date == _days_ago(90)
    assert _fallback("tin HPG năm ngoái", TODAY).start_date == _days_ago(365)


def test_fallback_relative_en():
    assert _fallback("HPG last week", TODAY).start_date == _days_ago(7)
    assert _fallback("HPG last month", TODAY).start_date == _days_ago(30)
    assert _fallback("HPG yesterday", TODAY).start_date == _days_ago(1)


def test_fallback_numeric():
    assert _fallback("HPG 3 ngày gần nhất", TODAY).start_date == _days_ago(3)
    assert _fallback("HPG 2 tuần gần đây", TODAY).start_date == _days_ago(14)
    assert _fallback("HPG 6 tháng qua", TODAY).start_date == _days_ago(180)


def test_fallback_explicit_is_true():
    assert _fallback("tin HPG tuần trước", TODAY).explicit is True


# ── LLM path (mocked) ──────────────────────────────────────────────────────────

def test_resolve_time_context_uses_llm_tool_call(monkeypatch):
    tc = MagicMock()
    tc.input = {"start_date": "2026-08-29", "end_date": "2026-09-05", "explicit": True}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    monkeypatch.setattr("core.time_context.create_client", lambda: client)

    out = resolve_time_context("tin HPG tuần trước", today=TODAY)
    assert out.start_date == "2026-08-29"
    assert out.end_date == "2026-09-05"
    assert out.explicit is True


def test_resolve_time_context_falls_back_on_llm_error(monkeypatch):
    def boom():
        raise RuntimeError("no llm")
    monkeypatch.setattr("core.time_context.create_client", boom)

    out = resolve_time_context("tin HPG tháng trước", today=TODAY)
    assert out.start_date == _days_ago(30)
    assert out.explicit is True


def test_resolve_time_context_falls_back_on_empty_toolcalls(monkeypatch):
    resp = MagicMock(); resp.tool_calls = []; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    monkeypatch.setattr("core.time_context.create_client", lambda: client)

    out = resolve_time_context("tin HPG hôm qua", today=TODAY)
    assert out.start_date == _days_ago(1)


# ── helpers ────────────────────────────────────────────────────────────────────

def test_to_days():
    assert to_days("2026-08-29", "2026-09-05") == 7
    assert to_days(None, None, default=7) == 7


def test_time_context_default_shape():
    tc = TimeContext(anchor=ISO, start_date=None, end_date=ISO, explicit=False)
    assert tc.anchor == ISO
    assert tc.start_date is None
