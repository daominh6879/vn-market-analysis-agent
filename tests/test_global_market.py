"""
tests/test_global_market.py — Unit tests for Phase 3 global market tools.

All network calls mocked. No DB required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tools.result import ToolResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_yf_close(tickers: list[str], n: int = 2) -> pd.DataFrame:
    """Fake multi-ticker Close DataFrame as yfinance.download returns."""
    import numpy as np
    data = {t: [100.0 + i * 0.5 for i in range(n)] for t in tickers}
    df = pd.DataFrame(data)
    df.columns = pd.Index(tickers)
    return df


# ── get_global_indices ────────────────────────────────────────────────────────

class TestGetGlobalIndices:
    def test_returns_tool_result_ok(self):
        from data.global_universe import WORLD_INDICES
        tickers = list(WORLD_INDICES.keys())[:3]
        fake_raw = MagicMock()
        fake_close = _make_yf_close(tickers, n=2)
        fake_raw.__getitem__ = lambda self, k: fake_close if k == "Close" else fake_close
        fake_raw.empty = False

        with patch("yfinance.download", return_value={"Close": fake_close}):
            from tools.global_market import get_global_indices
            result = get_global_indices()

        assert isinstance(result, ToolResult)

    def test_returns_no_data_on_empty(self):
        empty_df = pd.DataFrame()
        with patch("yfinance.download", return_value=empty_df):
            from tools.global_market import get_global_indices
            result = get_global_indices()
        assert result.status == "no_data"

    def test_data_contains_change_pct(self):
        from data.global_universe import WORLD_INDICES
        tickers = list(WORLD_INDICES.keys())[:2]

        # Day 0: 100, Day 1: 102 → +2%
        fake_close = pd.DataFrame(
            {t: [100.0, 102.0] for t in tickers},
            columns=pd.Index(tickers),
        )
        with patch("yfinance.download", return_value={"Close": fake_close}):
            from tools.global_market import get_global_indices
            result = get_global_indices()

        if result.status == "ok" and result.data:
            for item in result.data:
                assert "change_pct" in item
                assert "close" in item


# ── get_commodities ───────────────────────────────────────────────────────────

class TestGetCommodities:
    def test_returns_tool_result(self):
        from data.global_universe import COMMODITIES
        tickers = list(COMMODITIES.keys())
        fake_close = pd.DataFrame(
            {t: [1900.0, 1920.0] for t in tickers},
            columns=pd.Index(tickers),
        )
        with patch("yfinance.download", return_value={"Close": fake_close}):
            from tools.global_market import get_commodities
            result = get_commodities()
        assert isinstance(result, ToolResult)

    def test_unit_in_data(self):
        from data.global_universe import COMMODITIES
        tickers = list(COMMODITIES.keys())
        fake_close = pd.DataFrame(
            {t: [1900.0, 1920.0] for t in tickers},
            columns=pd.Index(tickers),
        )
        with patch("yfinance.download", return_value={"Close": fake_close}):
            from tools.global_market import get_commodities
            result = get_commodities()
        if result.status == "ok" and result.data:
            for item in result.data:
                assert "unit" in item


# ── get_crypto_prices ─────────────────────────────────────────────────────────

class TestGetCryptoPrices:
    def _fake_cg_response(self):
        return {
            "bitcoin":  {"usd": 80000.0, "usd_24h_change": 5.0, "usd_market_cap": 1.5e12},
            "ethereum": {"usd": 2478.0, "usd_24h_change": 1.3, "usd_market_cap": 3.0e11},
            "ripple":   {"usd": 1.48, "usd_24h_change": -1.0, "usd_market_cap": 8.5e10},
            "solana":   {"usd": 100.0, "usd_24h_change": 2.8, "usd_market_cap": 4.5e10},
        }

    def _fake_global_response(self):
        return {"data": {"total_market_cap": {"usd": 2.66e12}}}

    def test_returns_ok_with_coin_data(self):
        mock_price_resp = MagicMock()
        mock_price_resp.raise_for_status = MagicMock()
        mock_price_resp.json.return_value = self._fake_cg_response()
        mock_price_resp.status_code = 200

        mock_global_resp = MagicMock()
        mock_global_resp.status_code = 200
        mock_global_resp.json.return_value = self._fake_global_response()

        def fake_get(url, **kwargs):
            if "simple/price" in url:
                return mock_price_resp
            return mock_global_resp

        with patch("httpx.get", side_effect=fake_get):
            from tools.global_market import get_crypto_prices
            result = get_crypto_prices()

        assert result.status == "ok"
        assert result.data is not None
        coins = result.data["coins"]
        assert any(c["symbol"] == "BTC" for c in coins)
        btc = next(c for c in coins if c["symbol"] == "BTC")
        assert btc["price_usd"] == 80000.0
        assert btc["change_24h_pct"] == 5.0

    def test_total_mcap_in_data(self):
        mock_price_resp = MagicMock()
        mock_price_resp.raise_for_status = MagicMock()
        mock_price_resp.json.return_value = self._fake_cg_response()
        mock_price_resp.status_code = 200

        mock_global_resp = MagicMock()
        mock_global_resp.status_code = 200
        mock_global_resp.json.return_value = self._fake_global_response()

        def fake_get(url, **kwargs):
            if "simple/price" in url:
                return mock_price_resp
            return mock_global_resp

        with patch("httpx.get", side_effect=fake_get):
            from tools.global_market import get_crypto_prices
            result = get_crypto_prices()

        assert result.data["total_market_cap_trillion_usd"] == pytest.approx(2.66, abs=0.01)

    def test_handles_upstream_error(self):
        with patch("httpx.get", side_effect=Exception("timeout")):
            from tools.global_market import get_crypto_prices
            result = get_crypto_prices()
        assert result.status == "upstream_error"
        assert result.data is None


# ── get_fx_rates ──────────────────────────────────────────────────────────────

class TestGetFxRates:
    def test_returns_ok_with_vcb_data(self):
        fake_vcb = {
            "buy": 25920.0, "sell": 26330.0, "transfer": 26125.0,
            "timestamp": "2026-08-26T07:00:00", "source": "vietcombank",
        }
        with patch("data.fx_scraper.fetch_vcb_usdvnd", return_value=fake_vcb):
            from tools.global_market import get_fx_rates
            result = get_fx_rates()
        assert result.status == "ok"
        assert result.data["buy"] == 25920.0
        assert result.data["sell"] == 26330.0

    def test_returns_no_data_when_scraper_fails(self):
        with patch("data.fx_scraper.fetch_vcb_usdvnd", return_value=None):
            from tools.global_market import get_fx_rates
            result = get_fx_rates()
        assert result.status == "no_data"

    def test_message_contains_rates(self):
        fake_vcb = {"buy": 25920.0, "sell": 26330.0, "transfer": 26125.0,
                    "timestamp": "2026-08-26T07:00:00", "source": "vietcombank"}
        with patch("data.fx_scraper.fetch_vcb_usdvnd", return_value=fake_vcb):
            from tools.global_market import get_fx_rates
            result = get_fx_rates()
        assert "25,920" in result.message
        assert "26,330" in result.message


# ── get_vn_gold ───────────────────────────────────────────────────────────────

class TestGetVnGold:
    def test_returns_ok_with_sjc_data(self):
        fake_sjc = {
            "buy_vnd": 147.6, "sell_vnd": 150.6,
            "timestamp": "2026-08-26T07:00:00", "source": "sjc",
        }
        with patch("data.gold_vn_scraper.fetch_sjc_gold", return_value=fake_sjc), \
             patch("data.fx_scraper.fetch_vcb_usdvnd", return_value=None):
            from tools.global_market import get_vn_gold
            result = get_vn_gold()
        assert result.status == "ok"
        assert result.data["buy_vnd"] == 147.6

    def test_returns_no_data_when_sjc_fails(self):
        with patch("data.gold_vn_scraper.fetch_sjc_gold", return_value=None):
            from tools.global_market import get_vn_gold
            result = get_vn_gold()
        assert result.status == "no_data"

    def test_message_contains_prices(self):
        fake_sjc = {"buy_vnd": 147.6, "sell_vnd": 150.6,
                    "timestamp": "2026-08-26T07:00:00", "source": "sjc"}
        with patch("data.gold_vn_scraper.fetch_sjc_gold", return_value=fake_sjc), \
             patch("data.fx_scraper.fetch_vcb_usdvnd", return_value=None):
            from tools.global_market import get_vn_gold
            result = get_vn_gold()
        assert "147.6" in result.message
        assert "150.6" in result.message


# ── gold_vnd_per_oz conversion ────────────────────────────────────────────────

class TestGoldConversion:
    def test_conversion_approx(self):
        # Gold at 4624 USD/oz, USD/VND = 26125
        # 1 lượng = 1.20565 oz
        # VND/lượng = 4624 * 26125 * 1.20565 / 1_000_000 ≈ 145.5 triệu
        from data.gold_vn_scraper import gold_vnd_per_oz
        result = gold_vnd_per_oz(4624.0, 26125.0)
        assert 140.0 < result < 155.0

    def test_higher_gold_price_higher_vnd(self):
        from data.gold_vn_scraper import gold_vnd_per_oz
        low = gold_vnd_per_oz(4000.0, 26000.0)
        high = gold_vnd_per_oz(4700.0, 26000.0)
        assert high > low
