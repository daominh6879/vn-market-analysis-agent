"""Unit tests for portfolio/tcbs.py: snapshot parsing, opening-ledger build, token exchange (no network, no DB)."""
from __future__ import annotations

from datetime import date

import pytest

from portfolio import ledger, tcbs

ASSETS = {"assets": [{"symbol": "hpg", "quantity": 1000, "avgPrice": 25_000, "marketValue": 1},
                     {"symbol": "HPG", "quantity": 1000, "avgPrice": 27_000, "marketValue": 1},
                     {"symbol": "VNM", "quantity": 0, "avgPrice": 70_000, "marketValue": 0}]}
CASH = {"data": [{"accountNo": "other", "cashBalance": 1.0}, {"accountNo": "A1", "cashBalance": 50_000_000}]}


def test_parse_snapshot_merges_duplicates_skips_zero_qty_and_picks_account_cash():
    holdings, cash = tcbs.parse_snapshot(ASSETS, CASH, "A1")
    assert holdings == {"HPG": (2000, 26_000.0)} and cash == 50_000_000


def test_parse_snapshot_errors():
    with pytest.raises(tcbs.TcbsError):
        tcbs.parse_snapshot({"assets": [{"symbol": "HPG", "quantity": 100, "avgPrice": 0}]}, CASH, "A1")
    with pytest.raises(tcbs.TcbsError):
        tcbs.parse_snapshot(ASSETS, {"data": []}, "A1")


def test_build_txns_replays_to_tcbs_cash_and_positions():
    txns = tcbs.build_txns({"HPG": (2000, 26_000.0), "FPT": (100, 120_000.0)}, 50_000_000, date(2026, 10, 12))
    cash, holdings = ledger.replay(txns, [])
    assert cash == pytest.approx(50_000_000) and holdings["HPG"].qty == 2000
    assert holdings["HPG"].cost == pytest.approx(52_000_000)          # no fees: cost basis = avgPrice * qty
    assert all(t.note == tcbs.SYNC_NOTE for t in txns)


def test_build_txns_empty_account_raises():
    with pytest.raises(tcbs.TcbsError):
        tcbs.build_txns({}, 0, date(2026, 10, 12))


def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("TCBS_API_KEY", raising=False)
    with pytest.raises(tcbs.TcbsError):
        tcbs.sync("u", "123456")


class _R:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def test_token_exchange(monkeypatch):
    seen = {}
    monkeypatch.setattr(tcbs.httpx, "post", lambda url, json, timeout: seen.update(url=url, json=json) or _R(200, {"token": "t"}))
    assert tcbs.get_token("k", "123456") == "t" and seen["url"].endswith("/gaia/v1/oauth2/openapi/token")
    assert seen["json"] == {"apiKey": "k", "otp": "123456"}
    monkeypatch.setattr(tcbs.httpx, "post", lambda *a, **k: _R(401, {}))
    with pytest.raises(tcbs.TcbsError) as e:
        tcbs.get_token("secret-key", "1")
    assert "secret-key" not in str(e.value)
