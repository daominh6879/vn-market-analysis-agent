"""
tests/test_index_ingest.py — index ingest: foreign net coverage, no fake zeros. No network, no DB.
"""

from ingest import fetch_index as FI


def test_foreign_net_not_computable_for_exchange_indices():
    """HNX / UPCOM have no constituent flows → None (stored as NULL), never 0."""
    assert FI._foreign_net_by_date("HNX", "2026-10-01", "2026-10-09") is None
    assert FI._foreign_net_by_date("UPCOM", "2026-10-01", "2026-10-09") is None


def test_vn30_without_constituents_is_none(monkeypatch):
    monkeypatch.setattr(FI, "_index_members", lambda code: [])
    assert FI._foreign_net_by_date("VN30", "2026-10-01", "2026-10-09") is None


def test_exchange_indices_use_vnstock_symbols():
    assert FI._PROVIDER_SYMBOL == {"HNX": "HNXINDEX", "UPCOM": "UPCOMINDEX"}
