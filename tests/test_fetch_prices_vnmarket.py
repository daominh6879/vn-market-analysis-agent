"""tests/test_fetch_prices_vnmarket.py — ingest/fetch_prices.py reads bars through vnmarket (stubbed)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from ingest import fetch_prices as FP


def _conn():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_rows_are_nghin_dong_and_skip_missing_close():
    df = pd.DataFrame({
        "time": pd.to_datetime(["2026-10-12", "2026-10-13", "2026-10-14"]),
        "close": [20700.0, 20450.0, float("nan")],
        "volume": [31_127_600, 11_561_600, 5],
    })
    client = MagicMock()
    client.prices.history.return_value = df
    conn, cur = _conn()
    with patch.object(FP, "_latest_price_date", return_value=None), \
         patch("core.vnmarket_client.get_client", return_value=client), \
         patch.object(FP, "get_conn", return_value=conn):
        n = FP.fetch_and_insert("HPG", "2026-10-12", "2026-10-14")
    assert n == 2
    rows = cur.executemany.call_args.args[1]
    assert rows == [("HPG", "2026-10-12", 20.7, 31_127_600), ("HPG", "2026-10-13", 20.45, 11_561_600)]


def test_returns_zero_when_all_sources_fail():
    client = MagicMock()
    client.prices.history.side_effect = RuntimeError("all sources failed")
    with patch.object(FP, "_latest_price_date", return_value=None), \
         patch("core.vnmarket_client.get_client", return_value=client):
        assert FP.fetch_and_insert("HPG", "2026-10-12", "2026-10-14") == 0
