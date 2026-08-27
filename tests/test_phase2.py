"""
tests/test_phase2.py — Phase 2: foreign flows + sector performance.

Unit tests with mocks — no live DB, no network required.

Patch targets:
  - core.db.get_conn          → for foreign_flow_db layer
  - tools.foreign_flow_db.*   → for get_foreign_flows tool
  - tools.price._query_sector_performance_db / _fallback → for sector tool
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tools.result import ToolResult


# ── DB mock factory ────────────────────────────────────────────────────────────

def _make_conn_ctx(rows=None, col_names=None):
    """
    Build a (context-manager) mock for `get_conn()` that returns a connection
    whose cursor.fetchone / fetchall return the given rows.
    """
    rows = rows or []
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = rows[0] if rows else None
    mock_cur.fetchall.return_value = rows
    mock_cur.description = [(n,) for n in (col_names or [])]
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    ctx = MagicMock()
    ctx.__enter__ = lambda s: mock_conn
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ═══ 1. foreign_flow_db — query layer ════════════════════════════════════════

class TestForeignFlowDb:
    def test_query_market_foreign_net_ok(self):
        from tools.foreign_flow_db import query_market_foreign_net
        ctx = _make_conn_ctx([(500e9, 312e9, 188e9)])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_market_foreign_net(date(2026, 8, 25))
        assert result is not None
        assert result["total_buy"] == 500e9
        assert result["total_sell"] == 312e9
        assert result["net_value"] == 188e9
        assert result["date"] == "2026-08-25"

    def test_query_market_foreign_net_none_row_returns_none(self):
        from tools.foreign_flow_db import query_market_foreign_net
        ctx = _make_conn_ctx([(None, None, None)])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_market_foreign_net(date(2000, 1, 1))
        assert result is None

    def test_query_market_foreign_net_empty_table_returns_none(self):
        from tools.foreign_flow_db import query_market_foreign_net
        ctx = _make_conn_ctx([])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_market_foreign_net(date(2026, 8, 25))
        assert result is None

    def test_query_top_foreign_ok(self):
        from tools.foreign_flow_db import query_top_foreign
        cols = ["ticker", "buy_value", "sell_value", "net_value",
                "buy_volume", "sell_volume", "net_volume"]
        rows = [
            ("VIC", 1200e9, 200e9, 1000e9, 5_000_000, 800_000, 4_200_000),
            ("HPG", 800e9,  100e9, 700e9,  3_000_000, 400_000, 2_600_000),
        ]
        ctx = _make_conn_ctx(rows, cols)
        with patch("core.db.get_conn", return_value=ctx):
            result = query_top_foreign(date(2026, 8, 25), n=5, direction="buy")
        assert result is not None
        assert len(result) == 2
        assert result[0]["ticker"] == "VIC"

    def test_query_top_foreign_empty_returns_none(self):
        from tools.foreign_flow_db import query_top_foreign
        ctx = _make_conn_ctx([])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_top_foreign(date(2000, 1, 1), n=5)
        assert result is None

    def test_query_latest_foreign_date_ok(self):
        from tools.foreign_flow_db import query_latest_foreign_date
        ctx = _make_conn_ctx([(date(2026, 8, 25),)])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_latest_foreign_date()
        assert result == date(2026, 8, 25)

    def test_query_latest_foreign_date_empty_returns_none(self):
        from tools.foreign_flow_db import query_latest_foreign_date
        ctx = _make_conn_ctx([(None,)])
        with patch("core.db.get_conn", return_value=ctx):
            result = query_latest_foreign_date()
        assert result is None

    def test_upsert_foreign_rows_empty_returns_zero(self):
        from tools.foreign_flow_db import upsert_foreign_rows
        assert upsert_foreign_rows([]) == 0

    def test_upsert_foreign_rows_returns_count(self):
        from tools.foreign_flow_db import upsert_foreign_rows
        rows = [{
            "ticker": "VIC", "date": "2026-08-25",
            "buy_value": 1200e9, "sell_value": 200e9, "net_value": 1000e9,
            "buy_volume": 5_000_000, "sell_volume": 800_000, "net_volume": 4_200_000,
        }]
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        ctx = MagicMock()
        ctx.__enter__ = lambda s: mock_conn
        ctx.__exit__ = MagicMock(return_value=False)

        with patch("core.db.get_conn", return_value=ctx):
            n = upsert_foreign_rows(rows)
        assert n == 1

    def test_query_market_foreign_net_db_error_returns_none(self):
        from tools.foreign_flow_db import query_market_foreign_net
        with patch("core.db.get_conn", side_effect=RuntimeError("DB down")):
            result = query_market_foreign_net(date(2026, 8, 25))
        assert result is None


# ═══ 2. get_foreign_flows tool ════════════════════════════════════════════════
# Lazy imports inside price.py → patch at tools.foreign_flow_db.*

class TestGetForeignFlows:
    _MARKET = {
        "date": "2026-08-25",
        "total_buy": 500e9,
        "total_sell": 312e9,
        "net_value": 188e9,
    }
    _BUYERS = [
        {"ticker": "HPG", "buy_value": 150e9, "sell_value": 50e9, "net_value": 100e9},
        {"ticker": "VIC", "buy_value": 120e9, "sell_value": 30e9, "net_value": 90e9},
    ]
    _SELLERS = [
        {"ticker": "TCB", "buy_value": 20e9, "sell_value": 90e9, "net_value": -70e9},
    ]

    def test_ok_net_buy(self):
        from tools.price import get_foreign_flows
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=date(2026, 8, 25)),
            patch("tools.foreign_flow_db.query_market_foreign_net", return_value=self._MARKET),
            patch("tools.foreign_flow_db.query_top_foreign", side_effect=[self._BUYERS, self._SELLERS]),
        ):
            result = get_foreign_flows(days=1)
        assert result.status == "ok"
        assert "Mua ròng" in result.message
        assert "188" in result.message

    def test_ok_net_sell(self):
        from tools.price import get_foreign_flows
        market_sell = {**self._MARKET, "net_value": -500e9}
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=date(2026, 8, 25)),
            patch("tools.foreign_flow_db.query_market_foreign_net", return_value=market_sell),
            patch("tools.foreign_flow_db.query_top_foreign", side_effect=[self._BUYERS, self._SELLERS]),
        ):
            result = get_foreign_flows(days=1)
        assert result.status == "ok"
        assert "Bán ròng" in result.message

    def test_invalid_days_above_30(self):
        from tools.price import get_foreign_flows
        result = get_foreign_flows(days=99)
        assert result.status == "invalid_input"

    def test_invalid_days_zero(self):
        from tools.price import get_foreign_flows
        result = get_foreign_flows(days=0)
        assert result.status == "invalid_input"

    def test_no_db_date_triggers_live_fallback(self):
        from tools.price import get_foreign_flows
        live_result = ToolResult(
            status="no_data", data=None,
            message="Không có dữ liệu khối ngoại. DB rỗng và VCI không trả về dữ liệu."
        )
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=None),
            patch("tools.price._get_foreign_flows_live", return_value=live_result),
        ):
            result = get_foreign_flows(days=1)
        assert result.status == "no_data"

    def test_result_has_required_keys(self):
        from tools.price import get_foreign_flows
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=date(2026, 8, 25)),
            patch("tools.foreign_flow_db.query_market_foreign_net", return_value=self._MARKET),
            patch("tools.foreign_flow_db.query_top_foreign", side_effect=[self._BUYERS, self._SELLERS]),
        ):
            result = get_foreign_flows(days=1)
        required = {"date", "market_net_value", "market_net_value_bn",
                    "total_buy", "total_sell", "top_buyers", "top_sellers"}
        assert required.issubset(result.data.keys())

    def test_result_message_contains_top_tickers(self):
        from tools.price import get_foreign_flows
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=date(2026, 8, 25)),
            patch("tools.foreign_flow_db.query_market_foreign_net", return_value=self._MARKET),
            patch("tools.foreign_flow_db.query_top_foreign", side_effect=[self._BUYERS, self._SELLERS]),
        ):
            result = get_foreign_flows(days=1)
        assert "HPG" in result.message or "VIC" in result.message

    def test_net_value_bn_calculation(self):
        from tools.price import get_foreign_flows
        with (
            patch("tools.foreign_flow_db.query_latest_foreign_date", return_value=date(2026, 8, 25)),
            patch("tools.foreign_flow_db.query_market_foreign_net", return_value=self._MARKET),
            patch("tools.foreign_flow_db.query_top_foreign", side_effect=[self._BUYERS, self._SELLERS]),
        ):
            result = get_foreign_flows(days=1)
        assert result.data["market_net_value_bn"] == pytest.approx(188.0, abs=1.0)


# ═══ 3. _build_foreign_result helper ══════════════════════════════════════════

class TestBuildForeignResult:
    def test_net_buy_positive(self):
        from tools.price import _build_foreign_result
        buyers = [{"ticker": "HPG", "buy_value": 150e9, "sell_value": 50e9}]
        sellers = [{"ticker": "TCB", "buy_value": 20e9, "sell_value": 90e9}]
        result = _build_foreign_result("2026-08-25", 188e9, 500e9, 312e9, buyers, sellers)
        assert result.status == "ok"
        assert "Mua ròng" in result.message
        assert "188" in result.message

    def test_net_sell_negative(self):
        from tools.price import _build_foreign_result
        result = _build_foreign_result("2026-08-25", -300e9, 200e9, 500e9, [], [])
        assert "Bán ròng" in result.message
        assert "300" in result.message

    def test_data_has_correct_net_bn(self):
        from tools.price import _build_foreign_result
        result = _build_foreign_result("2026-08-25", 188e9, 500e9, 312e9, [], [])
        assert "market_net_value_bn" in result.data
        assert result.data["market_net_value_bn"] == pytest.approx(188.0, abs=1.0)

    def test_live_source_tag(self):
        from tools.price import _build_foreign_result
        result = _build_foreign_result("2026-08-25", 0, 0, 0, [], [], source="live")
        assert "(live)" in result.message


# ═══ 4. get_sector_performance tool ══════════════════════════════════════════

class TestGetSectorPerformance:
    _SECTORS = [
        {"sector": "Ngân hàng",    "pct_change": 1.5, "ticker_count": 20, "total_value_bn": 5000},
        {"sector": "Bất động sản", "pct_change": 0.8, "ticker_count": 15, "total_value_bn": 3000},
        {"sector": "Chứng khoán",  "pct_change": -0.3,"ticker_count": 10, "total_value_bn": 1500},
    ]

    def test_ok_from_db(self):
        from tools.price import get_sector_performance
        with patch("tools.price._query_sector_performance_db", return_value=self._SECTORS):
            result = get_sector_performance(period="day")
        assert result.status == "ok"
        assert "Ngân hàng" in result.message

    def test_fallback_when_db_empty(self):
        from tools.price import get_sector_performance
        with (
            patch("tools.price._query_sector_performance_db", return_value=None),
            patch("tools.price._query_sector_performance_fallback", return_value=self._SECTORS),
        ):
            result = get_sector_performance(period="day")
        assert result.status == "ok"

    def test_no_data_when_both_fail(self):
        from tools.price import get_sector_performance
        with (
            patch("tools.price._query_sector_performance_db", return_value=None),
            patch("tools.price._query_sector_performance_fallback", return_value=None),
        ):
            result = get_sector_performance(period="day")
        assert result.status == "no_data"

    def test_invalid_period_returns_invalid_input(self):
        from tools.price import get_sector_performance
        result = get_sector_performance(period="year")
        assert result.status == "invalid_input"
        assert "day" in result.message

    def test_data_shape_has_required_keys(self):
        from tools.price import get_sector_performance
        with patch("tools.price._query_sector_performance_db", return_value=self._SECTORS):
            result = get_sector_performance(period="day")
        required = {"sector", "pct_change", "ticker_count", "total_value_bn"}
        for item in result.data:
            assert required.issubset(item.keys())

    def test_message_includes_ticker_count(self):
        from tools.price import get_sector_performance
        with patch("tools.price._query_sector_performance_db", return_value=self._SECTORS):
            result = get_sector_performance(period="day")
        # Message should mention mã count
        assert "mã" in result.message

    def test_message_lists_all_sectors(self):
        from tools.price import get_sector_performance
        with patch("tools.price._query_sector_performance_db", return_value=self._SECTORS):
            result = get_sector_performance(period="day")
        for s in self._SECTORS:
            assert s["sector"] in result.message

    def test_no_data_message_is_actionable(self):
        from tools.price import get_sector_performance
        with (
            patch("tools.price._query_sector_performance_db", return_value=None),
            patch("tools.price._query_sector_performance_fallback", return_value=None),
        ):
            result = get_sector_performance(period="day")
        assert "ohlcv" in result.message.lower() or "dữ liệu" in result.message


# ═══ 5. ingest/fetch_foreign_flows.py ════════════════════════════════════════

class TestFetchForeignFlowsIngest:
    def test_no_rows_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_and_upsert
        with patch("ingest.fetch_foreign_flows.fetch_foreign_via_vci", return_value=[]):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 0

    def test_rows_upserted_returns_count(self):
        from ingest.fetch_foreign_flows import fetch_and_upsert
        mock_rows = [
            {"ticker": "VIC", "date": "2026-08-25",
             "buy_value": 1200e9, "sell_value": 200e9, "net_value": 1000e9,
             "buy_volume": 5_000_000, "sell_volume": 800_000, "net_volume": 4_200_000},
            {"ticker": "HPG", "date": "2026-08-25",
             "buy_value": 500e9, "sell_value": 400e9, "net_value": 100e9,
             "buy_volume": 2_000_000, "sell_volume": 1_600_000, "net_volume": 400_000},
        ]
        with (
            patch("ingest.fetch_foreign_flows.fetch_foreign_via_vci", return_value=mock_rows),
            patch("tools.foreign_flow_db.upsert_foreign_rows", return_value=2),
        ):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 2

    def test_network_exception_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_and_upsert
        with patch(
            "ingest.fetch_foreign_flows.fetch_foreign_via_vci",
            side_effect=RuntimeError("network error"),
        ):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 0

    def test_parse_date_default_is_today(self):
        from ingest.fetch_foreign_flows import _parse_date
        from datetime import date as date_cls
        result = _parse_date(None)
        assert result == date_cls.today()

    def test_parse_date_explicit(self):
        from ingest.fetch_foreign_flows import _parse_date
        result = _parse_date("2026-08-25")
        assert result == date(2026, 8, 25)


# ═══ 6. Registry ══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_get_foreign_flows_registered(self):
        from tools.registry import TOOL_REGISTRY
        assert "get_foreign_flows" in TOOL_REGISTRY

    def test_get_sector_performance_registered(self):
        from tools.registry import TOOL_REGISTRY
        assert "get_sector_performance" in TOOL_REGISTRY

    def test_get_foreign_flows_meta(self):
        from tools.registry import TOOL_REGISTRY
        meta = TOOL_REGISTRY["get_foreign_flows"]
        assert meta["cost_hint"] == "low"
        assert meta["side_effect"] is False
        assert "timeout" in meta

    def test_get_sector_performance_meta(self):
        from tools.registry import TOOL_REGISTRY
        meta = TOOL_REGISTRY["get_sector_performance"]
        assert meta["cost_hint"] == "low"
        assert meta["side_effect"] is False
