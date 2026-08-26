"""
tests/test_index_provider.py — Unit tests for SsiIndexProvider + index_db (Phase 0).

All network calls mocked. No DB connection required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools.providers import SsiIndexProvider, resolve_ticker, _detect_provider, _SSI_DIRECT


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ssi_response(symbol: str = "VNINDEX", n: int = 10) -> dict:
    """Build fake SSI iBoard response payload."""
    base_ts = int(datetime(2026, 8, 1).timestamp())
    day_sec = 86400
    return {
        "data": {
            "t": [base_ts + i * day_sec for i in range(n)],
            "o": [1750.0 + i for i in range(n)],
            "h": [1760.0 + i for i in range(n)],
            "l": [1740.0 + i for i in range(n)],
            "c": [1755.0 + i for i in range(n)],
            "v": [100_000_000 for _ in range(n)],
        }
    }


# ── resolve_ticker ────────────────────────────────────────────────────────────

class TestResolveTicker:
    def test_vn_index_alias(self):
        assert resolve_ticker("VN-INDEX") == "VNINDEX"
        assert resolve_ticker("HOSE") == "VNINDEX"

    def test_vn100_alias(self):
        assert resolve_ticker("VN100") == "VN30"

    def test_stock_ticker_passthrough(self):
        assert resolve_ticker("HPG") == "HPG"
        assert resolve_ticker("vcb") == "VCB"

    def test_vnindex_no_alias_needed(self):
        # VNINDEX maps to itself now (no proxy)
        assert resolve_ticker("VNINDEX") == "VNINDEX"


# ── _detect_provider ──────────────────────────────────────────────────────────

class TestDetectProvider:
    def test_vnindex_uses_ssi(self):
        p = _detect_provider("VNINDEX")
        assert isinstance(p, SsiIndexProvider)

    def test_vn30_uses_ssi(self):
        p = _detect_provider("VN30")
        assert isinstance(p, SsiIndexProvider)

    def test_hnx_uses_ssi(self):
        p = _detect_provider("HNX")
        assert isinstance(p, SsiIndexProvider)

    def test_stock_uses_vci(self):
        from tools.providers import VciDirectProvider
        p = _detect_provider("HPG")
        assert isinstance(p, VciDirectProvider)

    def test_intl_uses_yfinance(self):
        # _detect_provider routes by ticker format: dot or >4 chars → YFinance.
        # "^GSPC" (5 chars) matches that rule; "AAPL" (4 chars, no dot) routes to VCI.
        from tools.providers import YFinanceProvider
        p = _detect_provider("^GSPC")
        assert isinstance(p, YFinanceProvider)


# ── SsiIndexProvider ──────────────────────────────────────────────────────────

class TestSsiIndexProvider:
    def _mock_get(self, symbol: str = "VNINDEX", n: int = 10):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _make_ssi_response(symbol, n)
        return mock_resp

    def test_fetch_history_returns_dataframe(self):
        provider = SsiIndexProvider()
        with patch("httpx.get", return_value=self._mock_get("VNINDEX", 30)):
            df = provider.fetch_history("VNINDEX", 20)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
        assert len(df) <= 20

    def test_fetch_history_sorted_ascending(self):
        provider = SsiIndexProvider()
        with patch("httpx.get", return_value=self._mock_get("VNINDEX", 15)):
            df = provider.fetch_history("VNINDEX", 10)
        assert list(df["time"]) == sorted(df["time"].tolist())

    def test_fetch_price_returns_last_close(self):
        provider = SsiIndexProvider()
        fake = _make_ssi_response("VNINDEX", 5)
        last_close = fake["data"]["c"][-1]
        with patch("httpx.get", return_value=self._mock_get("VNINDEX", 5)):
            price = provider.fetch_price("VNINDEX")
        assert price == last_close

    def test_raises_on_empty_response(self):
        provider = SsiIndexProvider()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}}
        with patch("httpx.get", return_value=mock_resp):
            with pytest.raises(ValueError, match="No data"):
                provider.fetch_price("VNINDEX")

    def test_hnx_same_interface(self):
        provider = SsiIndexProvider()
        with patch("httpx.get", return_value=self._mock_get("HNX", 10)):
            df = provider.fetch_history("HNX", 5)
        assert not df.empty
        assert "close" in df.columns


# ── fetch_index (ingest) ──────────────────────────────────────────────────────

class TestFetchIndexIngest:
    def test_fetch_and_upsert_returns_count(self):
        fake_df = pd.DataFrame({
            "time": ["2026-08-25", "2026-08-26"],
            "open": [1785.0, 1789.0],
            "high": [1795.0, 1793.0],
            "low":  [1780.0, 1785.0],
            "close": [1788.78, 1791.41],
            "volume": [0, 0],
        })

        with patch("tools.providers.SsiIndexProvider.fetch_history", return_value=fake_df), \
             patch("tools.index_db.upsert_index_rows", return_value=2) as mock_upsert:
            from ingest.fetch_index import fetch_and_upsert
            n = fetch_and_upsert("VNINDEX", days=30)

        assert n == 2
        mock_upsert.assert_called_once()
        rows = mock_upsert.call_args[0][0]
        assert len(rows) == 2
        assert rows[0]["index_code"] == "VNINDEX"

    def test_change_pts_computed_correctly(self):
        fake_df = pd.DataFrame({
            "time": ["2026-08-25", "2026-08-26"],
            "open": [1785.0, 1789.0],
            "high": [1795.0, 1793.0],
            "low":  [1780.0, 1785.0],
            "close": [1788.78, 1791.41],
            "volume": [0, 0],
        })

        captured_rows = []

        def capture_upsert(rows):
            captured_rows.extend(rows)
            return len(rows)

        with patch("tools.providers.SsiIndexProvider.fetch_history", return_value=fake_df), \
             patch("tools.index_db.upsert_index_rows", side_effect=capture_upsert):
            from ingest.fetch_index import fetch_and_upsert
            fetch_and_upsert("VNINDEX", days=30)

        # First row: prev_close = None → change_pts = 0
        assert captured_rows[0]["change_pts"] == 0.0
        # Second row: 1791.41 - 1788.78 = 2.63
        assert abs(captured_rows[1]["change_pts"] - 2.63) < 0.01

    def test_returns_zero_on_fetch_error(self):
        with patch("tools.providers.SsiIndexProvider.fetch_history",
                   side_effect=Exception("network error")):
            from ingest.fetch_index import fetch_and_upsert
            n = fetch_and_upsert("VNINDEX", days=5)
        assert n == 0
