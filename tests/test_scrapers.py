"""
tests/test_scrapers.py — Unit tests for FX and gold scrapers (now backed by vnmarket).

The vnmarket client is stubbed; no network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _client(fx=None, gold=None, fx_exc=None, gold_exc=None):
    c = MagicMock()
    if fx_exc:
        c.macro.fx.side_effect = fx_exc
    else:
        c.macro.fx.return_value = fx
    if gold_exc:
        c.macro.gold.side_effect = gold_exc
    else:
        c.macro.gold.return_value = gold
    return c


# ── VCB FX scraper ────────────────────────────────────────────────────────────

_FX = pd.DataFrame([
    {"currency": "USD", "name": "USD", "buy_cash": 25920.0, "buy_transfer": 26125.0, "sell": 26330.0},
    {"currency": "EUR", "name": "EUR", "buy_cash": 28100.0, "buy_transfer": 28650.0, "sell": 29200.0},
])


class TestFxScraper:
    def test_returns_usd_rates(self):
        with patch("core.vnmarket_client.get_client", return_value=_client(fx=_FX)):
            from data.fx_scraper import fetch_vcb_usdvnd
            result = fetch_vcb_usdvnd()
        assert result["buy"] == 25920.0
        assert result["sell"] == 26330.0
        assert result["transfer"] == 26125.0
        assert result["source"] == "vietcombank"

    def test_returns_none_on_source_error(self):
        with patch("core.vnmarket_client.get_client", return_value=_client(fx_exc=Exception("all sources failed"))):
            from data.fx_scraper import fetch_vcb_usdvnd
            assert fetch_vcb_usdvnd() is None

    def test_returns_none_on_missing_usd(self):
        with patch("core.vnmarket_client.get_client", return_value=_client(fx=_FX[_FX.currency == "EUR"])):
            from data.fx_scraper import fetch_vcb_usdvnd
            assert fetch_vcb_usdvnd() is None

    def test_returns_none_when_usd_has_no_buy_sell(self):
        mid_only = pd.DataFrame([{"currency": "USD", "name": "USD", "buy_cash": None,
                                  "buy_transfer": None, "sell": None, "mid": 26000.0}])
        with patch("core.vnmarket_client.get_client", return_value=_client(fx=mid_only)):
            from data.fx_scraper import fetch_vcb_usdvnd
            assert fetch_vcb_usdvnd() is None

    def test_midpoint_rate(self):
        from data.fx_scraper import midpoint_rate
        assert midpoint_rate({"buy": 25920.0, "sell": 26330.0, "transfer": 26125.0}) == 26125.0


# ── SJC gold scraper ──────────────────────────────────────────────────────────

def _gold_df():
    df = pd.DataFrame([
        {"brand": "SJC", "type": "Vàng nhẫn SJC 99,99% 1 chỉ", "branch": "Hồ Chí Minh", "buy": 140.8e6, "sell": 143.8e6},
        {"brand": "SJC", "type": "Vàng SJC 1L, 10L, 1KG", "branch": "Miền Bắc", "buy": 141.0e6, "sell": 144.0e6},
        {"brand": "SJC", "type": "Vàng SJC 1L, 10L, 1KG", "branch": "Hồ Chí Minh", "buy": 141.3e6, "sell": 144.3e6},
    ])
    df.attrs["source"] = "sjc"
    return df


class TestGoldScraper:
    def test_returns_buy_sell_vnd(self):
        with patch("core.vnmarket_client.get_client", return_value=_client(gold=_gold_df())):
            from data.gold_vn_scraper import fetch_sjc_gold
            result = fetch_sjc_gold()
        assert result["buy_vnd"] == pytest.approx(141.3, abs=0.01)
        assert result["sell_vnd"] == pytest.approx(144.3, abs=0.01)
        assert result["source"] == "sjc"

    def test_returns_none_on_error(self):
        # fetch_sjc_gold falls back to a yfinance world-price estimate; disable it so
        # "every source failed" is what's under test (and no live network call is made).
        with patch("core.vnmarket_client.get_client", return_value=_client(gold_exc=Exception("network"))), \
             patch("data.gold_vn_scraper._fetch_yfinance_gold_estimate", return_value=None):
            from data.gold_vn_scraper import fetch_sjc_gold
            assert fetch_sjc_gold() is None
