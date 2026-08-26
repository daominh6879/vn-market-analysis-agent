"""
tests/test_p1_breadth_movers.py — Unit tests for Phase 1:
  - hose_universe (load/seed)
  - get_market_breadth(universe="HOSE"|"VN30")
  - get_top_movers(by="value"|"pct_gain"|"pct_loss")
  - query_top_by_value (ohlcv_db extension)
  - _build_breadth_result label propagation

All DB and network calls mocked. No live connections required.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from tools.result import ToolResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_universe_df(tickers: list[str], advances: int, declines: int) -> pd.DataFrame:
    """Build fake query_universe_latest result."""
    total = len(tickers)
    rows = []
    for i, t in enumerate(tickers[:total]):
        if i < advances:
            pct = 1.5 + i * 0.1
        elif i < advances + declines:
            pct = -(1.0 + i * 0.1)
        else:
            pct = 0.0
        rows.append({"ticker": t, "date": "2026-08-26",
                     "close": 50_000.0 + i * 100, "prev_close": 50_000.0,
                     "pct_change": round(pct, 2)})
    return pd.DataFrame(rows)


def _make_top_value_df(tickers: list[str]) -> pd.DataFrame:
    rows = [
        {
            "ticker": t,
            "date": "2026-08-26",
            "close": 200_000.0 - i * 1000,
            "volume": 10_000_000 - i * 500_000,
            "traded_value": (200_000.0 - i * 1000) * (10_000_000 - i * 500_000),
            "prev_close": 198_000.0,
            "pct_change": 1.0 - i * 0.1,
        }
        for i, t in enumerate(tickers)
    ]
    return pd.DataFrame(rows)


# ── hose_universe ─────────────────────────────────────────────────────────────

class TestHoseUniverse:
    def test_load_hose_tickers_returns_list(self):
        from data.hose_universe import load_hose_tickers
        tickers = load_hose_tickers()
        assert isinstance(tickers, list)
        assert len(tickers) >= 100
        assert all(isinstance(t, str) for t in tickers)

    def test_seed_contains_vn30(self):
        from data.hose_universe import HOSE_SEED, get_vn30_tickers
        vn30 = get_vn30_tickers()
        assert "VCB" in vn30
        assert "HPG" in vn30
        assert len(vn30) >= 20

    def test_load_hose_universe_has_sector(self):
        from data.hose_universe import load_hose_universe
        universe = load_hose_universe()
        assert len(universe) >= 100
        for item in universe[:5]:
            assert "ticker" in item
            assert "sector" in item
            assert "index_member" in item

    def test_no_duplicates_in_seed(self):
        from data.hose_universe import HOSE_SEED
        tickers = [t for t, _, _ in HOSE_SEED]
        assert len(tickers) == len(set(tickers)), "Duplicate tickers in HOSE_SEED"

    def test_fetch_and_save_returns_zero_on_error(self):
        import httpx
        with patch("httpx.post", side_effect=Exception("network")):
            from data.hose_universe import fetch_and_save_hose_universe
            n = fetch_and_save_hose_universe()
        assert n == 0


# ── _build_breadth_result label ───────────────────────────────────────────────

class TestBuildBreadthLabel:
    def test_label_hose_in_summary(self):
        from tools.price import _build_breadth_result
        changes = [
            {"ticker": "A", "pct_change": 1.0, "close": 50_000.0, "volume": 0},
            {"ticker": "B", "pct_change": -1.0, "close": 48_000.0, "volume": 0},
        ]
        result = _build_breadth_result(changes, label="HOSE")
        assert "HOSE" in result.message

    def test_label_vn30_in_summary(self):
        from tools.price import _build_breadth_result
        changes = [{"ticker": "HPG", "pct_change": 2.0, "close": 30_000.0, "volume": 0}]
        result = _build_breadth_result(changes, label="VN30")
        assert "VN30" in result.message


# ── get_market_breadth ────────────────────────────────────────────────────────

class TestGetMarketBreadthHose:
    def _seed_tickers(self):
        from data.hose_universe import load_hose_tickers
        return load_hose_tickers()[:30]  # 30 for speed

    def test_hose_breadth_from_db(self):
        tickers = self._seed_tickers()
        fake_df = _make_universe_df(tickers, advances=15, declines=12)

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_universe_latest", return_value=fake_df):
            from tools.price import get_market_breadth
            result = get_market_breadth(universe="HOSE")

        assert result.status == "ok"
        assert result.data["advances"] == 15
        assert result.data["declines"] == 12
        assert result.data["unchanged"] == len(tickers) - 15 - 12
        assert "HOSE" in result.message

    def test_vn30_breadth_from_db(self):
        from tools.price import _VN30_CONSTITUENTS
        fake_df = _make_universe_df(_VN30_CONSTITUENTS, advances=20, declines=8)

        with patch("tools.ohlcv_db.query_universe_latest", return_value=fake_df):
            from tools.price import get_market_breadth
            result = get_market_breadth(universe="VN30")

        assert result.status == "ok"
        assert result.data["advances"] == 20
        assert "VN30" in result.message

    def test_returns_no_data_when_db_and_api_fail(self):
        tickers = self._seed_tickers()

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_universe_latest", return_value=None), \
             patch("tools.providers.VciDirectProvider.fetch_batch_latest",
                   side_effect=Exception("network")):
            from tools.price import get_market_breadth
            result = get_market_breadth(universe="HOSE")

        assert result.status == "no_data"

    def test_breadth_has_top_gainers_and_losers(self):
        tickers = self._seed_tickers()
        fake_df = _make_universe_df(tickers, advances=10, declines=10)

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_universe_latest", return_value=fake_df):
            from tools.price import get_market_breadth
            result = get_market_breadth()

        assert "top_gainers" in result.data
        assert "top_losers" in result.data
        assert len(result.data["top_gainers"]) <= 5
        assert len(result.data["top_losers"]) <= 5


# ── get_top_movers ────────────────────────────────────────────────────────────

class TestGetTopMovers:
    def _tickers(self):
        return ["VIC", "HPG", "VCB", "TCB", "MBB", "FPT", "VNM"]

    def test_top_value_from_db(self):
        tickers = self._tickers()
        fake_df = _make_top_value_df(tickers[:5])

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_top_by_value", return_value=fake_df):
            from tools.price import get_top_movers
            result = get_top_movers(by="value", limit=5)

        assert result.status == "ok"
        assert len(result.data) == 5
        assert result.data[0]["ticker"] == tickers[0]
        assert "traded_value" in result.data[0]
        assert "value" in result.message.lower() or "thanh" in result.message.lower()

    def test_top_pct_gain(self):
        tickers = self._tickers()
        fake_df = _make_universe_df(tickers, advances=5, declines=2)

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_universe_latest", return_value=fake_df):
            from tools.price import get_top_movers
            result = get_top_movers(by="pct_gain", limit=3)

        assert result.status == "ok"
        assert len(result.data) <= 3
        # Results should be sorted descending pct_change
        pcts = [r["pct_change"] for r in result.data]
        assert pcts == sorted(pcts, reverse=True)

    def test_top_pct_loss(self):
        tickers = self._tickers()
        fake_df = _make_universe_df(tickers, advances=2, declines=5)

        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_universe_latest", return_value=fake_df):
            from tools.price import get_top_movers
            result = get_top_movers(by="pct_loss", limit=3)

        assert result.status == "ok"
        assert len(result.data) <= 3
        pcts = [r["pct_change"] for r in result.data]
        assert pcts == sorted(pcts)  # ascending = most negative first

    def test_invalid_by_param(self):
        from tools.price import get_top_movers
        result = get_top_movers(by="random")
        assert result.status == "invalid_input"

    def test_no_data_when_db_empty(self):
        tickers = self._tickers()
        with patch("data.hose_universe.load_hose_tickers", return_value=tickers), \
             patch("tools.ohlcv_db.query_top_by_value", return_value=None):
            from tools.price import get_top_movers
            result = get_top_movers(by="value")
        assert result.status == "no_data"


# ── fetch_ohlcv --universe flag (arg parsing only) ───────────────────────────

class TestFetchOhlcvUniverse:
    def test_vn30_shortcut_returns_vn30_tickers(self):
        from data.hose_universe import get_vn30_tickers
        tickers = get_vn30_tickers()
        assert "VCB" in tickers
        assert "HPG" in tickers
        assert 25 <= len(tickers) <= 35

    def test_hose_universe_larger_than_vn30(self):
        from data.hose_universe import load_hose_tickers, get_vn30_tickers
        hose = load_hose_tickers()
        vn30 = get_vn30_tickers()
        assert len(hose) > len(vn30)
