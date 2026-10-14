"""tests/test_vnmarket_tools.py — vnmarket-backed live tools. Client stubbed; no network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from vnmarket import NoData, RateLimited

from tools import vnmarket_tools as T


def _patch(client):
    return patch("core.vnmarket_client.get_client", return_value=client)


def _board():
    return pd.DataFrame([{
        "symbol": "HPG", "ref": 20700.0, "price": 20450.0, "volume": 11_561_600.0, "value": 237_151_440_000.0,
        "foreign_buy_value": 7_295_590_000.0, "foreign_sell_value": 28_715_036_250.0,
    }])


def test_quotes_ok_and_reports_missing_symbols():
    c = MagicMock()
    c.market.quotes.return_value = _board()
    with _patch(c):
        r = T.get_live_quotes(["hpg", "zzz"])
    c.market.quotes.assert_called_once_with(["HPG", "ZZZ"])
    assert r.status == "ok"
    assert "HPG: 20,450 VND (-1.21%" in r.message and "Không có dữ liệu: ZZZ" in r.message


def test_quotes_empty_input_is_invalid():
    assert T.get_live_quotes([" ", ""]).status == "invalid_input"


@pytest.mark.parametrize("exc,status", [
    (NoData("nothing"), "no_data"),
    (RateLimited("429"), "rate_limited"),
    (RuntimeError("boom"), "upstream_error"),
])
def test_failures_map_to_toolresult_status(exc, status):
    c = MagicMock()
    c.market.quotes.side_effect = exc
    with _patch(c):
        assert T.get_live_quotes(["HPG"]).status == status


def test_proprietary_formats_billions():
    c = MagicMock()
    c.market.proprietary.return_value = pd.DataFrame([{
        "date": pd.Timestamp("2026-10-13"), "symbol": "HPG", "buy_value": 45_416_865_000, "sell_value": 50_224_350_000}])
    with _patch(c):
        r = T.get_proprietary_trading("hpg", days=500)
    c.market.proprietary.assert_called_once_with("HPG", limit=90)   # days clamped
    assert "2026-10-13: mua 45.4 / bán 50.2 tỷ" in r.message


def test_company_profile_unknown_ticker_is_no_data():
    c = MagicMock()
    c.company.overview.return_value = {"market_cap": None}
    c.company.profile.return_value = {"business_summary": ""}
    with _patch(c):
        assert T.get_company_profile("QQQQQ").status == "no_data"


def test_company_profile_strips_html():
    c = MagicMock()
    c.company.overview.return_value = {"market_cap": 1.7e14, "highest_price1_year": 26000.0,
                                       "lowest_price1_year": 19000.0, "foreigner_percentage": 0.2}
    c.company.profile.return_value = {"business_summary": "<p>- Thép xây dựng.</p><p>- Tôn mạ.</p>"}
    with _patch(c):
        r = T.get_company_profile("HPG")
    assert "<p>" not in r.message and "Thép xây dựng" in r.message and "NN sở hữu 20.0%" in r.message


def test_sector_performance_sorted_desc():
    c = MagicMock()
    df = pd.DataFrame({"name": ["A", "B"], "change_pct": [-1.0, 2.0]})
    df.attrs["source"] = "kbs"
    c.market.sectors_performance.return_value = df
    with _patch(c):
        r = T.get_live_sector_performance()
    assert r.message.index("B: +2.00%") < r.message.index("A: -1.00%")


def test_tools_are_registered():
    from tools.registry import TOOL_REGISTRY
    for name in ("get_live_quotes", "get_intraday_ticks", "get_vn_index_board",
                 "get_live_sector_performance", "get_proprietary_trading", "get_company_profile"):
        assert name in TOOL_REGISTRY
