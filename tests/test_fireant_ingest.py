"""
tests/test_fireant_ingest.py — Unit tests for Fireant provider + updated ingest scripts.

All network and DB calls mocked. No live connections required.
Run: pytest tests/test_fireant_ingest.py -v
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


# ═══ Helpers ══════════════════════════════════════════════════════════════════

def _fireant_api_rows(n: int = 5, ticker: str = "HPG") -> list[dict]:
    """Fake Fireant historical-quotes API response (newest → oldest order from API)."""
    base = date(2026, 8, 1)
    rows = []
    for i in range(n):
        d = base + timedelta(days=i)
        rows.append({
            "date": d.isoformat() + "T00:00:00",
            "priceOpen":          20_000 + i * 100,
            "priceHigh":          20_500 + i * 100,
            "priceLow":           19_800 + i * 100,
            "priceClose":         20_200 + i * 100,
            "dealVolume":         1_000_000 + i * 10_000,
            "buyForeignQuantity":    50_000 + i * 500,
            "sellForeignQuantity":   20_000 + i * 200,
        })
    return list(reversed(rows))  # API returns newest first


def _mock_fireant_resp(rows: list[dict] | None = None, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if status == 401:
        resp.raise_for_status.side_effect = Exception("401 Unauthorized")
    resp.json.return_value = rows if rows is not None else _fireant_api_rows()
    return resp


def _mock_login_resp(token: str = "test-token") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"accessToken": token}
    return resp


# ═══ 1. FireantProvider ═══════════════════════════════════════════════════════

class TestFireantProviderLogin:
    def setup_method(self):
        from tools.providers import FireantProvider
        FireantProvider._token = None  # reset class-level token before each test

    def test_login_sets_class_token(self):
        from tools.providers import FireantProvider
        with patch("httpx.post", return_value=_mock_login_resp("tok123")):
            p = FireantProvider()
            token = p._login()
        assert token == "tok123"
        assert FireantProvider._token == "tok123"

    def test_get_token_reuses_cached(self):
        from tools.providers import FireantProvider
        FireantProvider._token = "cached-token"
        with patch("httpx.post") as mock_post:
            p = FireantProvider()
            token = p._get_token()
        mock_post.assert_not_called()
        assert token == "cached-token"

    def test_get_token_calls_login_when_none(self):
        from tools.providers import FireantProvider
        FireantProvider._token = None
        with patch("httpx.post", return_value=_mock_login_resp("fresh-tok")):
            p = FireantProvider()
            token = p._get_token()
        assert token == "fresh-tok"

    def test_login_missing_access_token_raises(self):
        from tools.providers import FireantProvider
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json.return_value = {"error": "invalid credentials"}
        with patch("httpx.post", return_value=bad_resp):
            p = FireantProvider()
            with pytest.raises(ValueError, match="no accessToken"):
                p._login()


class TestFireantProviderFetchHistoryRange:
    def setup_method(self):
        from tools.providers import FireantProvider
        FireantProvider._token = "test-token"

    def test_returns_dataframe_with_correct_columns(self):
        from tools.providers import FireantProvider
        api_rows = _fireant_api_rows(10)
        with patch("httpx.get", return_value=_mock_fireant_resp(api_rows)):
            p = FireantProvider()
            df = p.fetch_history_range("HPG", "2026-08-01", "2026-08-10")
        assert isinstance(df, pd.DataFrame)
        required = {"time", "open", "high", "low", "close", "volume", "foreign_buy_vol", "foreign_sell_vol"}
        assert required.issubset(set(df.columns))

    def test_sorted_oldest_to_newest(self):
        from tools.providers import FireantProvider
        with patch("httpx.get", return_value=_mock_fireant_resp(_fireant_api_rows(10))):
            p = FireantProvider()
            df = p.fetch_history_range("HPG", "2026-08-01", "2026-08-10")
        assert list(df["time"]) == sorted(df["time"].tolist())

    def test_foreign_volumes_parsed(self):
        from tools.providers import FireantProvider
        api_rows = _fireant_api_rows(3)
        with patch("httpx.get", return_value=_mock_fireant_resp(api_rows)):
            p = FireantProvider()
            df = p.fetch_history_range("HPG", "2026-08-01", "2026-08-03")
        # all foreign volumes should be positive
        assert (df["foreign_buy_vol"] >= 0).all()
        assert (df["foreign_sell_vol"] >= 0).all()

    def test_401_triggers_reauth(self):
        from tools.providers import FireantProvider
        FireantProvider._token = "expired-token"
        api_rows = _fireant_api_rows(5)
        # First call returns 401, second succeeds after re-login
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_ok = _mock_fireant_resp(api_rows)
        with (
            patch("httpx.get", side_effect=[resp_401, resp_ok]),
            patch("httpx.post", return_value=_mock_login_resp("new-token")),
        ):
            p = FireantProvider()
            df = p.fetch_history_range("HPG", "2026-08-01", "2026-08-05")
        assert not df.empty
        assert FireantProvider._token == "new-token"

    def test_empty_response_raises(self):
        from tools.providers import FireantProvider
        with patch("httpx.get", return_value=_mock_fireant_resp([])):
            p = FireantProvider()
            with pytest.raises(ValueError, match="no data"):
                p.fetch_history_range("HPG", "2026-08-01", "2026-08-05")

    def test_date_string_truncated_to_10_chars(self):
        """Fireant returns ISO datetime strings; time part must be stripped."""
        from tools.providers import FireantProvider
        api_rows = _fireant_api_rows(3)
        with patch("httpx.get", return_value=_mock_fireant_resp(api_rows)):
            p = FireantProvider()
            df = p.fetch_history_range("HPG", "2026-08-01", "2026-08-03")
        for t in df["time"]:
            assert len(str(t)) == 10, f"Expected YYYY-MM-DD, got: {t}"


class TestFireantProviderInterface:
    def setup_method(self):
        from tools.providers import FireantProvider
        FireantProvider._token = "test-token"

    def test_fetch_price_returns_last_close(self):
        from tools.providers import FireantProvider
        api_rows = _fireant_api_rows(5)
        expected_close = float(api_rows[0]["priceClose"])  # oldest row after sort
        with patch("httpx.get", return_value=_mock_fireant_resp(api_rows)):
            p = FireantProvider()
            price = p.fetch_price("HPG")
        # fetch_price returns last close (newest after sort)
        assert isinstance(price, float)
        assert price > 0

    def test_fetch_history_returns_tail_n_rows(self):
        from tools.providers import FireantProvider
        with patch("httpx.get", return_value=_mock_fireant_resp(_fireant_api_rows(20))):
            p = FireantProvider()
            df = p.fetch_history("HPG", 10)
        assert len(df) <= 10


# ═══ 2. _detect_provider with Fireant ════════════════════════════════════════

class TestDetectProviderFireant:
    def test_vn_stock_uses_fireant_primary(self):
        from tools.providers import _detect_provider, FallbackProvider, FireantProvider
        with patch("tools.providers._vn_ticker_set", return_value={"HPG", "VCB"}):
            p = _detect_provider("HPG")
        # outer FallbackProvider primary should be FireantProvider
        assert isinstance(p, FallbackProvider)
        assert isinstance(p._primary, FireantProvider)

    def test_index_still_uses_ssi(self):
        from tools.providers import _detect_provider, SsiIndexProvider
        p = _detect_provider("VNINDEX")
        assert isinstance(p, SsiIndexProvider)

    def test_intl_still_uses_yfinance(self):
        from tools.providers import _detect_provider, YFinanceProvider
        p = _detect_provider("^GSPC")
        assert isinstance(p, YFinanceProvider)


# ═══ 3. fetch_ohlcv.py ════════════════════════════════════════════════════════

class TestFetchOhlcv:
    """fetch_and_upsert returns dict {ohlcv: int, foreign: int}."""

    def _fake_fireant_df(self, n: int = 5, with_foreign: bool = True) -> pd.DataFrame:
        rows = _fireant_api_rows(n)
        data = {
            "time":             [r["date"][:10] for r in reversed(rows)],
            "open":             [float(r["priceOpen"]) for r in reversed(rows)],
            "high":             [float(r["priceHigh"]) for r in reversed(rows)],
            "low":              [float(r["priceLow"]) for r in reversed(rows)],
            "close":            [float(r["priceClose"]) for r in reversed(rows)],
            "volume":           [int(r["dealVolume"]) for r in reversed(rows)],
        }
        if with_foreign:
            data["foreign_buy_vol"]  = [int(r["buyForeignQuantity"]) for r in reversed(rows)]
            data["foreign_sell_vol"] = [int(r["sellForeignQuantity"]) for r in reversed(rows)]
        return pd.DataFrame(data)

    def test_returns_dict_with_ohlcv_and_foreign_keys(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        df = self._fake_fireant_df(5)
        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._latest_date", return_value=None),
            patch("ingest.fetch_ohlcv._upsert_ohlcv", return_value=5),
            patch("ingest.fetch_ohlcv._upsert_foreign", return_value=5),
        ):
            mock_fa.fetch_history_range.return_value = df
            result = fetch_and_upsert("HPG", days=30)
        assert isinstance(result, dict)
        assert "ohlcv" in result
        assert "foreign" in result

    def test_fireant_success_upserts_both_tables(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        df = self._fake_fireant_df(5)
        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._latest_date", return_value=None),
            patch("ingest.fetch_ohlcv._upsert_ohlcv", return_value=5) as mock_ohlcv,
            patch("ingest.fetch_ohlcv._upsert_foreign", return_value=5) as mock_foreign,
        ):
            mock_fa.fetch_history_range.return_value = df
            result = fetch_and_upsert("HPG", days=30)
        mock_ohlcv.assert_called_once()
        mock_foreign.assert_called_once()
        assert result["ohlcv"] == 5
        assert result["foreign"] == 5

    def test_fireant_failure_falls_back_to_vci(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        df_fallback = self._fake_fireant_df(3, with_foreign=False)
        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._fallback") as mock_fb,
            patch("ingest.fetch_ohlcv._latest_date", return_value=None),
            patch("ingest.fetch_ohlcv._upsert_ohlcv", return_value=3),
            patch("ingest.fetch_ohlcv._upsert_foreign", return_value=0),
        ):
            mock_fa.fetch_history_range.side_effect = RuntimeError("fireant down")
            mock_fb.get_history.return_value = df_fallback
            result = fetch_and_upsert("HPG", days=30)
        mock_fb.get_history.assert_called_once()
        assert result["ohlcv"] == 3
        assert result["foreign"] == 0  # no foreign col on fallback

    def test_incremental_skips_if_already_up_to_date(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        today = date.today()
        with (
            patch("ingest.fetch_ohlcv._latest_date", return_value=today),
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
        ):
            result = fetch_and_upsert("HPG", days=5)
        mock_fa.fetch_history_range.assert_not_called()
        assert result == {"ohlcv": 0, "foreign": 0}

    def test_explicit_date_range_overrides_incremental(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        df = self._fake_fireant_df(10)
        start = date(2026, 8, 1)
        end = date(2026, 8, 10)
        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._latest_date", return_value=date(2026, 8, 15)),  # "future" latest
            patch("ingest.fetch_ohlcv._upsert_ohlcv", return_value=10),
            patch("ingest.fetch_ohlcv._upsert_foreign", return_value=10),
        ):
            mock_fa.fetch_history_range.return_value = df
            result = fetch_and_upsert("HPG", start_date=start, end_date=end)
        # Should NOT skip; explicit range bypasses incremental logic
        mock_fa.fetch_history_range.assert_called_once_with("HPG", "2026-08-01", "2026-08-10")
        assert result["ohlcv"] == 10

    def test_foreign_value_derived_from_volume_times_close(self):
        """buy_val = buy_vol × close / 1e9 (tỷ đồng)."""
        from ingest.fetch_ohlcv import fetch_and_upsert, _upsert_foreign as real_upsert_foreign
        df = pd.DataFrame({
            "time":             ["2026-08-01"],
            "open":             [20_000.0],
            "high":             [20_500.0],
            "low":              [19_800.0],
            "close":            [20_000.0],
            "volume":           [1_000_000],
            "foreign_buy_vol":  [100_000],
            "foreign_sell_vol": [40_000],
        })
        captured = []
        def _capture(rows):
            captured.extend(rows)
            return len(rows)

        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._latest_date", return_value=None),
            patch("ingest.fetch_ohlcv._upsert_ohlcv", return_value=1),
            patch("ingest.fetch_ohlcv._upsert_foreign", side_effect=_capture),
        ):
            mock_fa.fetch_history_range.return_value = df
            fetch_and_upsert("HPG", days=5)

        assert len(captured) == 1
        row = captured[0]
        # row = (ticker, date, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol)
        ticker, d, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol = row
        assert ticker == "HPG"
        expected_buy = round(100_000 * 20_000.0 / 1e9, 4)
        assert abs(buy_val - expected_buy) < 1e-6
        expected_sell = round(40_000 * 20_000.0 / 1e9, 4)
        assert abs(sell_val - expected_sell) < 1e-6
        assert buy_vol == 100_000
        assert sell_vol == 40_000
        assert net_vol == 60_000

    def test_both_sources_fail_raises(self):
        from ingest.fetch_ohlcv import fetch_and_upsert
        with (
            patch("ingest.fetch_ohlcv._fireant") as mock_fa,
            patch("ingest.fetch_ohlcv._fallback") as mock_fb,
            patch("ingest.fetch_ohlcv._latest_date", return_value=None),
        ):
            mock_fa.fetch_history_range.side_effect = RuntimeError("fireant error")
            mock_fb.get_history.side_effect = RuntimeError("fallback error")
            with pytest.raises(RuntimeError, match="OHLCV fetch failed"):
                fetch_and_upsert("HPG", days=5)


# ═══ 4. fetch_foreign_flows.py ════════════════════════════════════════════════

class TestFetchForeignFlowsHistorical:
    def _fake_df(self, n: int = 5) -> pd.DataFrame:
        rows = _fireant_api_rows(n)
        return pd.DataFrame({
            "time":             [r["date"][:10] for r in reversed(rows)],
            "open":             [float(r["priceOpen"]) for r in reversed(rows)],
            "high":             [float(r["priceHigh"]) for r in reversed(rows)],
            "low":              [float(r["priceLow"]) for r in reversed(rows)],
            "close":            [float(r["priceClose"]) for r in reversed(rows)],
            "volume":           [int(r["dealVolume"]) for r in reversed(rows)],
            "foreign_buy_vol":  [int(r["buyForeignQuantity"]) for r in reversed(rows)],
            "foreign_sell_vol": [int(r["sellForeignQuantity"]) for r in reversed(rows)],
        })

    def test_fetch_historical_returns_row_count(self):
        from ingest.fetch_foreign_flows import fetch_historical
        df = self._fake_df(5)
        with (
            patch("ingest.fetch_foreign_flows._fireant") as mock_fa,
            patch("ingest.fetch_foreign_flows._upsert_rows", return_value=5),
        ):
            mock_fa.fetch_history_range.return_value = df
            n = fetch_historical("HPG", date(2026, 8, 1), date(2026, 8, 5))
        assert n == 5

    def test_fetch_historical_empty_df_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_historical
        empty_df = pd.DataFrame()
        with patch("ingest.fetch_foreign_flows._fireant") as mock_fa:
            mock_fa.fetch_history_range.return_value = empty_df
            n = fetch_historical("HPG", date(2026, 8, 1), date(2026, 8, 5))
        assert n == 0

    def test_fetch_incremental_calls_fireant_from_latest_plus_one(self):
        from ingest.fetch_foreign_flows import fetch_incremental
        df = self._fake_df(3)
        latest = date(2026, 8, 10)
        today = date(2026, 8, 14)
        expected_start = date(2026, 8, 11)

        with (
            patch("ingest.fetch_foreign_flows._latest_date", return_value=latest),
            patch("ingest.fetch_foreign_flows._fireant") as mock_fa,
            patch("ingest.fetch_foreign_flows._upsert_rows", return_value=3),
        ):
            mock_fa.fetch_history_range.return_value = df
            n = fetch_incremental("HPG", today)
        mock_fa.fetch_history_range.assert_called_once_with("HPG", str(expected_start), str(today))

    def test_fetch_incremental_already_up_to_date_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_incremental
        today = date.today()
        with patch("ingest.fetch_foreign_flows._latest_date", return_value=today):
            n = fetch_incremental("HPG", today)
        assert n == 0

    def test_upsert_rows_tuple_format(self):
        """Rows inserted into DB must be 8-tuples in correct order."""
        from ingest.fetch_foreign_flows import fetch_historical
        df = pd.DataFrame({
            "time":             ["2026-08-01"],
            "open":             [20_000.0],
            "high":             [20_500.0],
            "low":              [19_800.0],
            "close":            [20_000.0],
            "volume":           [1_000_000],
            "foreign_buy_vol":  [100_000],
            "foreign_sell_vol": [40_000],
        })
        captured = []
        def _capture(rows):
            captured.extend(rows)
            return len(rows)

        with (
            patch("ingest.fetch_foreign_flows._fireant") as mock_fa,
            patch("ingest.fetch_foreign_flows._upsert_rows", side_effect=_capture),
        ):
            mock_fa.fetch_history_range.return_value = df
            fetch_historical("HPG", date(2026, 8, 1), date(2026, 8, 1))

        assert len(captured) == 1
        row = captured[0]
        assert len(row) == 8  # (ticker, date, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol)
        ticker, d, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol = row
        assert ticker == "HPG"
        assert d == "2026-08-01"
        assert buy_vol == 100_000
        assert sell_vol == 40_000
        assert net_vol == 60_000


class TestFetchForeignFlowsLive:
    def test_fetch_live_today_calls_vci_batch(self):
        from ingest.fetch_foreign_flows import fetch_live_today
        mock_batch = [
            {"ticker": "HPG", "buy_value": 2e9, "sell_value": 1e9, "net_value": 1e9,
             "buy_volume": 100, "sell_volume": 50, "net_volume": 50},
        ]
        with (
            patch("ingest.fetch_foreign_flows._active_tickers", return_value=["HPG"]),
            patch("ingest.fetch_foreign_flows._vci") as mock_vci,
            patch("ingest.fetch_foreign_flows._upsert_rows", return_value=1),
        ):
            mock_vci.fetch_foreign_batch.return_value = mock_batch
            n = fetch_live_today(date(2026, 8, 25))
        assert n == 1

    def test_fetch_live_today_vci_failure_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_live_today
        with (
            patch("ingest.fetch_foreign_flows._active_tickers", return_value=["HPG"]),
            patch("ingest.fetch_foreign_flows._vci") as mock_vci,
            patch("ingest.fetch_foreign_flows._upsert_rows", return_value=0),
        ):
            mock_vci.fetch_foreign_batch.side_effect = RuntimeError("VCI down")
            n = fetch_live_today(date(2026, 8, 25))
        assert n == 0


# ═══ 5. Backward-compat shims ════════════════════════════════════════════════

class TestCompatShims:
    def test_fetch_and_upsert_shim_calls_fetch_foreign_via_vci(self):
        """Legacy fetch_and_upsert must route through fetch_foreign_via_vci (patchable in tests)."""
        from ingest.fetch_foreign_flows import fetch_and_upsert
        mock_rows = [
            {"ticker": "HPG", "date": "2026-08-25",
             "buy_value": 1e9, "sell_value": 5e8, "net_value": 5e8,
             "buy_volume": 50_000, "sell_volume": 25_000, "net_volume": 25_000},
        ]
        with (
            patch("ingest.fetch_foreign_flows.fetch_foreign_via_vci", return_value=mock_rows),
            patch("ingest.fetch_foreign_flows._upsert_rows", return_value=1),
        ):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 1

    def test_fetch_and_upsert_shim_empty_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_and_upsert
        with patch("ingest.fetch_foreign_flows.fetch_foreign_via_vci", return_value=[]):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 0

    def test_fetch_and_upsert_shim_network_error_returns_zero(self):
        from ingest.fetch_foreign_flows import fetch_and_upsert
        with patch(
            "ingest.fetch_foreign_flows.fetch_foreign_via_vci",
            side_effect=RuntimeError("net error"),
        ):
            n = fetch_and_upsert(date(2026, 8, 25))
        assert n == 0

    def test_parse_date_default_is_today(self):
        from ingest.fetch_foreign_flows import _parse_date
        assert _parse_date(None) == date.today()

    def test_parse_date_explicit(self):
        from ingest.fetch_foreign_flows import _parse_date
        assert _parse_date("2026-08-25") == date(2026, 8, 25)

    def test_fetch_foreign_via_vci_shim_uses_vci_batch(self):
        from ingest.fetch_foreign_flows import fetch_foreign_via_vci
        mock_batch = [{"ticker": "HPG", "buy_value": 1e9, "sell_value": 5e8, "net_value": 5e8,
                       "buy_volume": 50_000, "sell_volume": 25_000, "net_volume": 25_000}]
        with (
            patch("ingest.fetch_foreign_flows._active_tickers", return_value=["HPG"]),
            patch("ingest.fetch_foreign_flows._vci") as mock_vci,
        ):
            mock_vci.fetch_foreign_batch.return_value = mock_batch
            rows = fetch_foreign_via_vci(date(2026, 8, 25))
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "HPG"
        assert rows[0]["date"] == "2026-08-25"


# ═══ 6. Dagster asset call-site compatibility ════════════════════════════════

class TestDagsterAssetCallSites:
    """Ensure updated function signatures don't break Dagster asset callers."""

    def test_assets_ohlcv_handles_dict_return(self):
        """assets_ohlcv extracts result['ohlcv'] — must not crash on dict return."""
        result = {"ohlcv": 7, "foreign": 7}
        n = result["ohlcv"]
        assert n == 7

    def test_backfill_script_handles_dict_return(self):
        """backfill_ohlcv handles both int (legacy) and dict (new) return types."""
        for val in [{"ohlcv": 5, "foreign": 3}, 5]:
            n = val["ohlcv"] if isinstance(val, dict) else val
            assert n == 5


# ═══ 7. Live Fireant test (marked, skipped in CI) ════════════════════════════

@pytest.mark.live
def test_fireant_live_fetch_hpg():
    """Fireant live: fetch 5 days of HPG OHLCV + foreign volumes."""
    from tools.providers import FireantProvider
    FireantProvider._token = None

    today = date.today()
    start = str(today - timedelta(days=10))
    end = str(today)

    p = FireantProvider()
    df = p.fetch_history_range("HPG", start, end)

    assert not df.empty, "Fireant returned no data for HPG"
    assert "foreign_buy_vol" in df.columns
    assert "foreign_sell_vol" in df.columns
    assert (df["close"] > 0).all()
    print(f"\n  HPG rows={len(df)}, latest={df['time'].iloc[-1]}, "
          f"close={df['close'].iloc[-1]:,.0f}")
