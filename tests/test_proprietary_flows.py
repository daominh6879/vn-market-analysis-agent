"""Unit tests for ingest/fetch_proprietary_flows (no network, no DB)."""
from __future__ import annotations

import contextlib
from datetime import date

import pandas as pd
import pytest

from ingest import fetch_proprietary_flows as fpf
from vnmarket.errors import NoData


def _frame():
    return pd.DataFrame([{"date": pd.Timestamp("2026-10-16"), "symbol": "HPG", "buy_volume": 4168600, "sell_volume": 1070400,
                          "buy_value": 86766695000, "sell_value": 22233215000, "net_volume": 3098200, "net_value": 64533480000}])


def test_to_rows_converts_vnd_to_billions():
    row = fpf.to_rows(_frame())[0]
    assert row == ("HPG", date(2026, 10, 16), 86.7667, 22.2332, 64.5335, 4168600, 1070400, 3098200)


@contextlib.contextmanager
def _fake_vnmarket(*a, **k):
    yield object()


def _patch(monkeypatch, fetch):
    import vnmarket
    monkeypatch.setattr(vnmarket, "VnMarket", _fake_vnmarket)
    monkeypatch.setattr(fpf, "_latest_dates", lambda: {})
    monkeypatch.setattr(fpf, "_upsert", lambda rows: len(rows))
    monkeypatch.setattr(fpf, "fetch_ticker", fetch)


def test_run_raises_when_most_tickers_return_nothing(monkeypatch):
    def fetch(vn, t, start):
        if t == "AAA":
            return _frame()
        raise NoData("none")
    _patch(monkeypatch, fetch)
    with pytest.raises(RuntimeError, match="only 1/4"):
        fpf.run(["AAA", "BBB", "CCC", "DDD"])


def test_run_tolerates_names_without_proprietary_activity(monkeypatch):
    def fetch(vn, t, start):
        if t == "DDD":
            raise NoData("none")
        return _frame()
    _patch(monkeypatch, fetch)
    out = fpf.run(["AAA", "BBB", "CCC", "DDD"])
    assert out["tickers_ok"] == 3 and out["no_data"] == 1 and out["rows"] == 3 and out["failed"] == []


def test_incremental_start_overlaps_stored_tail(monkeypatch):
    seen = {}
    def fetch(vn, t, start):
        seen[t] = start
        return _frame()
    _patch(monkeypatch, fetch)
    monkeypatch.setattr(fpf, "_latest_dates", lambda: {"AAA": date(2026, 10, 10)})
    fpf.run(["AAA", "BBB"])
    assert seen["AAA"] == date(2026, 10, 5) and seen["BBB"] == fpf.HISTORY_START
