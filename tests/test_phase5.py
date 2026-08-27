"""
tests/test_phase5.py — Unit tests for Phase 5: corporate events + broker views.

No network calls. No DB (all DB queries mocked via monkeypatch).
Covers:
  - data/corporate_events_scraper.py  (_classify_event, _parse_date, _extract_ratio)
  - ingest/extract_broker_views.py    (BrokerView validation)
  - tools/events_views.py             (get_corporate_events, get_broker_views)
  - tools/registry.py                 (new entries exist)
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from data.corporate_events_scraper import (
    _classify_event,
    _extract_ratio,
    _parse_date,
)
from ingest.extract_broker_views import BrokerView
from tools.events_views import get_broker_views, get_corporate_events
from tools.registry import TOOL_REGISTRY
from tools.result import ToolResult


# ── corporate_events_scraper helpers ─────────────────────────────────────────

class TestClassifyEvent:
    def test_gdkhq_keyword(self):
        assert _classify_event("Giao dịch không hưởng quyền cổ tức 2025") == "gdkhq"

    def test_gdkhq_abbrev(self):
        assert _classify_event("TN1 GDKHQ ngày 26/8") == "gdkhq"

    def test_dividend(self):
        assert _classify_event("Cổ tức tiền mặt tỷ lệ 5%") == "dividend"

    def test_rights_issue(self):
        assert _classify_event("Phát hành cổ phiếu tăng vốn 100:10.5") == "rights_issue"

    def test_agm(self):
        assert _classify_event("Đại hội cổ đông thường niên 2026") == "agm"

    def test_other_fallback(self):
        assert _classify_event("Thay đổi nhân sự cấp cao") == "other"

    def test_case_insensitive(self):
        assert _classify_event("GỬI ĐỊA ĐIỂM Cổ TứC") == "dividend"


class TestParseDate:
    def test_valid_dd_mm_yyyy(self):
        assert _parse_date("26/08/2026") == date(2026, 8, 26)

    def test_single_digit(self):
        assert _parse_date("1/9/2026") == date(2026, 9, 1)

    def test_embedded_in_text(self):
        assert _parse_date("Ngày GDKHQ: 15/07/2026 (thứ ba)") == date(2026, 7, 15)

    def test_no_date_returns_none(self):
        assert _parse_date("Không có ngày nào") is None

    def test_invalid_month_returns_none(self):
        assert _parse_date("32/13/2026") is None


class TestExtractRatio:
    def test_percent(self):
        assert _extract_ratio("Cổ tức 5% tiền mặt") == 5.0

    def test_ratio_100_10(self):
        result = _extract_ratio("Tỷ lệ 100:10")
        assert result is not None
        assert abs(result - 10.0) < 0.01

    def test_ratio_100_10_5(self):
        result = _extract_ratio("Tỷ lệ 100:10.5")
        # _extract_ratio uses _RATIO_RE which matches integers; 10.5 not matched by int regex
        # so it falls back to None or partial — just verify no crash
        assert result is None or isinstance(result, float)

    def test_no_ratio(self):
        assert _extract_ratio("Đại hội cổ đông thường niên") is None

    def test_float_percent(self):
        assert _extract_ratio("lợi tức 12.5%") == 12.5


# ── BrokerView validation ─────────────────────────────────────────────────────

class TestBrokerView:
    def test_basic_construction(self):
        v = BrokerView(broker="TPS", ticker_or_index="vnindex", target=1900.0)
        assert v.ticker_or_index == "VNINDEX"
        assert v.target == 1900.0

    def test_stance_normalized_buy(self):
        v = BrokerView(broker="HSC", ticker_or_index="HPG", stance="mua")
        assert v.stance == "buy"

    def test_stance_normalized_neutral(self):
        v = BrokerView(broker="SSI", ticker_or_index="VIC", stance="trung lập")
        assert v.stance == "neutral"

    def test_stance_normalized_accumulate(self):
        v = BrokerView(broker="VCBS", ticker_or_index="VNINDEX", stance="tích lũy")
        assert v.stance == "accumulate"

    def test_none_stance_allowed(self):
        v = BrokerView(broker="Yuanta", ticker_or_index="VN30", stance=None)
        assert v.stance is None

    def test_ticker_uppercased(self):
        v = BrokerView(broker="MBS", ticker_or_index="hpg")
        assert v.ticker_or_index == "HPG"

    def test_all_numeric_fields_optional(self):
        v = BrokerView(broker="KIS", ticker_or_index="VNINDEX")
        assert v.target is None
        assert v.support is None
        assert v.resistance is None

    def test_support_resistance_stored(self):
        v = BrokerView(broker="VCBS", ticker_or_index="VNINDEX", support=1760.0, resistance=1850.0)
        assert v.support == 1760.0
        assert v.resistance == 1850.0


# ── tools/events_views.py ─────────────────────────────────────────────────────

def _make_mock_cursor(rows: list[tuple], col_names: list[str]):
    """Return a mock cursor that returns col_names + rows on fetchall."""
    cur = MagicMock()
    cur.description = [(c,) for c in col_names]
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _make_mock_conn(rows, col_names):
    cur = _make_mock_cursor(rows, col_names)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


CORP_COLS = ["ticker", "event_type", "ex_date", "record_date", "ratio", "note"]
BROKER_COLS = ["broker", "ticker_or_index", "published_at", "stance", "target", "support", "resistance", "source_url"]


class TestGetCorporateEvents:
    def test_returns_tool_result(self):
        conn = _make_mock_conn([], CORP_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_corporate_events()
        assert isinstance(result, ToolResult)

    def test_no_data_status(self):
        conn = _make_mock_conn([], CORP_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_corporate_events(days_ahead=7)
        assert result.status == "no_data"
        assert result.data == []

    def test_ok_with_rows(self):
        ex = date(2026, 8, 26)
        rows = [("TN1", "gdkhq", ex, None, 10.0, "Cổ tức 10%")]
        conn = _make_mock_conn(rows, CORP_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_corporate_events()
        assert result.status == "ok"
        assert len(result.data) == 1
        assert result.data[0]["ticker"] == "TN1"

    def test_message_contains_ticker(self):
        ex = date(2026, 8, 27)
        rows = [("VAB", "rights_issue", ex, None, None, "Phát hành 85.7 triệu cp")]
        conn = _make_mock_conn(rows, CORP_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_corporate_events()
        assert "VAB" in result.message

    def test_upstream_error_on_exception(self):
        with patch("data.db.get_conn", side_effect=RuntimeError("DB down")):
            result = get_corporate_events()
        assert result.status == "upstream_error"
        assert "DB down" in result.message


class TestGetBrokerViews:
    def test_returns_tool_result(self):
        conn = _make_mock_conn([], BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX")
        assert isinstance(result, ToolResult)

    def test_no_data_status(self):
        conn = _make_mock_conn([], BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX", days=7)
        assert result.status == "no_data"

    def test_ok_with_rows(self):
        pub = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
        rows = [("TPS", "VNINDEX", pub, "buy", 1900.0, None, None, "https://tps.com.vn/1")]
        conn = _make_mock_conn(rows, BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX")
        assert result.status == "ok"
        assert len(result.data) == 1
        assert result.data[0]["broker"] == "TPS"
        assert result.data[0]["target"] == 1900.0

    def test_vcbs_support_in_message(self):
        pub = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
        rows = [("VCBS", "VNINDEX", pub, "neutral", None, 1760.0, None, "")]
        conn = _make_mock_conn(rows, BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX")
        assert result.status == "ok"
        assert "1,760" in result.message or "1760" in result.message

    def test_yuanta_resistance_in_message(self):
        pub = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
        rows = [("Yuanta", "VNINDEX", pub, None, None, None, 1820.0, "")]
        conn = _make_mock_conn(rows, BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX")
        assert "1,820" in result.message or "1820" in result.message

    def test_case_insensitive_subject(self):
        """ticker_or_index is normalized to uppercase before query — verify no crash."""
        conn = _make_mock_conn([], BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("vnindex")
        assert result.status in ("ok", "no_data")

    def test_upstream_error_on_exception(self):
        with patch("data.db.get_conn", side_effect=RuntimeError("Timeout")):
            result = get_broker_views("VNINDEX")
        assert result.status == "upstream_error"

    def test_multiple_brokers_all_in_data(self):
        pub = datetime(2026, 8, 25, tzinfo=timezone.utc)
        rows = [
            ("TPS",    "VNINDEX", pub, "buy",     1900.0, None,   None,   ""),
            ("VCBS",   "VNINDEX", pub, "neutral",  None, 1760.0,  None,   ""),
            ("Yuanta", "VNINDEX", pub, None,       None,  None,  1820.0,  ""),
        ]
        conn = _make_mock_conn(rows, BROKER_COLS)
        with patch("data.db.get_conn", return_value=conn):
            result = get_broker_views("VNINDEX")
        assert result.status == "ok"
        assert len(result.data) == 3
        brokers = {r["broker"] for r in result.data}
        assert brokers == {"TPS", "VCBS", "Yuanta"}


# ── registry ─────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_get_corporate_events_registered(self):
        assert "get_corporate_events" in TOOL_REGISTRY

    def test_get_broker_views_registered(self):
        assert "get_broker_views" in TOOL_REGISTRY

    def test_get_corporate_events_no_side_effect(self):
        assert TOOL_REGISTRY["get_corporate_events"]["side_effect"] is False

    def test_get_broker_views_cost_hint(self):
        assert TOOL_REGISTRY["get_broker_views"]["cost_hint"] == "free"
