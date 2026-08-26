"""
tests/test_scrapers.py — Unit tests for FX and gold scrapers (Phase 3).

Mock network calls. Verify parsing logic against representative XML fixtures.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── VCB FX scraper ────────────────────────────────────────────────────────────

_VCB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<root>
  <DateTime>8/26/2026 7:00:01 AM</DateTime>
  <Exrate CurrencyCode="USD" CurrencyName="USD" Buy="25,920" Sell="26,330" Transfer="26,125"/>
  <Exrate CurrencyCode="EUR" CurrencyName="EUR" Buy="28,100" Sell="29,200" Transfer="28,650"/>
</root>"""


class TestFxScraper:
    def _mock_resp(self, content: bytes = _VCB_XML, status: int = 200):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.status_code = status
        mock.content = content
        return mock

    def test_returns_usd_rates(self):
        with patch("httpx.get", return_value=self._mock_resp()):
            from data.fx_scraper import fetch_vcb_usdvnd
            result = fetch_vcb_usdvnd()
        assert result is not None
        assert result["buy"] == 25920.0
        assert result["sell"] == 26330.0
        assert result["transfer"] == 26125.0
        assert result["source"] == "vietcombank"

    def test_returns_none_on_http_error(self):
        mock = MagicMock()
        mock.raise_for_status.side_effect = Exception("HTTP 503")
        with patch("httpx.get", return_value=mock):
            from data.fx_scraper import fetch_vcb_usdvnd
            result = fetch_vcb_usdvnd()
        assert result is None

    def test_returns_none_on_missing_usd(self):
        xml_no_usd = b"""<?xml version="1.0"?>
        <root><Exrate CurrencyCode="EUR" Buy="28000" Sell="29000" Transfer="28500"/></root>"""
        with patch("httpx.get", return_value=self._mock_resp(xml_no_usd)):
            from data.fx_scraper import fetch_vcb_usdvnd
            result = fetch_vcb_usdvnd()
        assert result is None

    def test_midpoint_rate(self):
        from data.fx_scraper import midpoint_rate
        data = {"buy": 25920.0, "sell": 26330.0, "transfer": 26125.0}
        mid = midpoint_rate(data)
        assert mid == 26125.0


# ── SJC gold scraper ──────────────────────────────────────────────────────────

# SJC XML format (approximate — actual format may vary)
_SJC_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<root>
  <item type="SJC" khu_vuc="TP.HCM" buy="147,600,000" sell="150,600,000"/>
  <item type="SJC" khu_vuc="Ha Noi"  buy="147,600,000" sell="150,600,000"/>
</root>"""


class TestGoldScraper:
    def _mock_resp(self, content: bytes = _SJC_XML):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.content = content
        return mock

    def test_returns_buy_sell_vnd(self):
        with patch("httpx.get", return_value=self._mock_resp()):
            from data.gold_vn_scraper import fetch_sjc_gold
            result = fetch_sjc_gold()
        assert result is not None
        assert result["buy_vnd"] == pytest.approx(147.6, abs=0.1)
        assert result["sell_vnd"] == pytest.approx(150.6, abs=0.1)
        assert result["source"] == "sjc"

    def test_returns_none_on_error(self):
        mock = MagicMock()
        mock.raise_for_status.side_effect = Exception("network")
        with patch("httpx.get", return_value=mock):
            from data.gold_vn_scraper import fetch_sjc_gold
            result = fetch_sjc_gold()
        assert result is None


import pytest
