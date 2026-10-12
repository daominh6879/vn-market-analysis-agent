"""
tests/test_price_units.py — unit consistency of price outputs.

Convention: tools return VN stock prices in VND; ohlcv_daily stores nghìn đồng (VCB 56.7);
indices are in points; foreign flows / traded value in tỷ đồng.
No network, no DB.
"""

import pandas as pd
import pytest

from data.corporate_events_scraper import _extract_ratio
from tools import providers as P
from tools.price import get_historical_ohlcv, get_realtime_price
from tools.providers import PriceProvider, YFinanceProvider


def _thousand_df() -> pd.DataFrame:
    """What Fireant / KBS return: nghìn đồng."""
    return pd.DataFrame({
        "time": ["2026-10-08", "2026-10-09"],
        "open": [57.0, 56.6], "high": [57.3, 57.0], "low": [56.6, 56.2], "close": [56.6, 56.7],
        "volume": [2272500, 3049500],
    })


class _FixedProvider(PriceProvider):
    def __init__(self, price: float):
        self._price = price

    def fetch_price(self, ticker):
        return self._price

    def fetch_history(self, ticker, days):
        return pd.DataFrame({"time": ["2026-10-09"], "open": [1.0], "high": [1.0],
                             "low": [1.0], "close": [self._price], "volume": [1]})


@pytest.fixture
def _no_db_first(monkeypatch):
    monkeypatch.setattr("tools.ohlcv_db.query_ohlcv", lambda *a, **k: None)


# ── providers: Fireant / KBS are nghìn đồng natively → normalized to VND ───────

def test_fireant_fetch_history_and_price_return_vnd(monkeypatch):
    fa = P.FireantProvider()
    monkeypatch.setattr(fa, "fetch_history_range", lambda *a, **k: _thousand_df())
    assert fa.fetch_price("VCB") == 56_700
    df = fa.fetch_history("VCB", 2)
    assert df["close"].tolist() == [56_600, 56_700]
    assert df["volume"].tolist() == [2272500, 3049500]  # volume untouched


def test_fireant_range_stays_raw_for_ingest(monkeypatch):
    """ingest/fetch_ohlcv.py stores fetch_history_range as-is (nghìn đồng)."""
    fa = P.FireantProvider()
    monkeypatch.setattr(fa, "_get_token", lambda: "t")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [{"date": "2026-10-09T00:00:00", "priceOpen": 56.6, "priceHigh": 57.0,
                     "priceLow": 56.2, "priceClose": 56.7, "dealVolume": 1}]

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    df = fa.fetch_history_range("VCB", "2026-10-09", "2026-10-09")
    assert df["close"].iloc[-1] == pytest.approx(56.7)


def test_kbs_returns_vnd(monkeypatch):
    kbs = P.KbsProvider()
    monkeypatch.setattr(kbs, "_kbs_history", lambda *a, **k: _thousand_df())
    assert kbs.fetch_price("VCB") == 56_700
    assert kbs.fetch_history("VCB", 2)["close"].iloc[-1] == 56_700


# ── get_realtime_price message carries the right unit ─────────────────────────

def test_realtime_price_stock_is_vnd(monkeypatch):
    monkeypatch.setattr(P, "_in_trading_hours", lambda: False)
    r = get_realtime_price("VCB", provider=_FixedProvider(56_700))
    assert r.message == "Giá VCB: 56,700 VND (phiên gần nhất)."


def test_realtime_price_in_session_label(monkeypatch):
    monkeypatch.setattr(P, "_in_trading_hours", lambda: True)
    r = get_realtime_price("VCB", provider=_FixedProvider(56_700))
    assert r.message == "Giá VCB: 56,700 VND (đang giao dịch)."


def test_realtime_price_index_is_points():
    r = get_realtime_price("VNINDEX", provider=_FixedProvider(1735.09))
    assert "điểm" in r.message and "VND" not in r.message


def test_realtime_price_non_vn_symbol_is_usd():
    class _Yf(YFinanceProvider):
        def fetch_price(self, ticker):
            return 189.5

    r = get_realtime_price("AAPL", provider=_Yf())
    assert "USD" in r.message and "VND" not in r.message


@pytest.mark.usefixtures("_no_db_first")
def test_historical_message_unit_for_index():
    r = get_historical_ohlcv("VNINDEX", days=1, provider=_FixedProvider(1735.0))
    assert "(điểm)" in r.message


# ── corporate event ratio ─────────────────────────────────────────────────────

@pytest.mark.parametrize("note,expected", [
    ("Trả cổ tức năm 2025 bằng cổ phiếu (tỷ lệ 100:5)", 5.0),
    ("Thưởng cổ phiếu (tỷ lệ 20:1)", 5.0),
    ("Thưởng cp (tỷ lệ 10:8)", 80.0),
    ("Trả cổ tức bằng cổ phiếu (tỷ lệ 1,1125%)", 1.1125),   # VN decimal comma
    ("Cổ tức 5%", 5.0),
    ("Chi trả cổ tức năm 2025 bằng tiền (2.000 đ/cp)", None),  # '2025' is not 20:25
    ("Tổ chức Đại hội đồng cổ đông bất thường năm 2026", None),
    ("Giao dịch 3.049.523 cp trả cổ tức năm 2025", None),
])
def test_extract_ratio(note, expected):
    assert _extract_ratio(note) == expected


# ── ohlcv_daily (nghìn đồng) → VND ────────────────────────────────────────────

def _fake_get_conn(rows):
    class _Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda: _Conn()


def test_query_ohlcv_scales_stock_rows_to_vnd(monkeypatch):
    from datetime import date
    from tools.ohlcv_db import query_ohlcv
    rows = [(date(2026, 10, 9), 56.6, 57.0, 56.2, 56.7, 3049500),
            (date(2026, 10, 8), 57.0, 57.3, 56.6, 56.6, 2272500)]
    monkeypatch.setattr("core.db.get_conn", _fake_get_conn(rows))
    df = query_ohlcv("VCB", 2)
    assert df["close"].tolist() == [56_600, 56_700]
    assert df["open"].iloc[-1] == 56_600


def test_query_ohlcv_mixed_units_row_wise(monkeypatch):
    """Legacy rows already in VND (>= 1000) must not be scaled twice."""
    from datetime import date
    from tools.ohlcv_db import query_ohlcv
    rows = [(date(2025, 9, 9), 7.25, 7.3, 7.2, 7.25, 3100),
            (date(2025, 9, 8), 6981.52, 7000.0, 6900.0, 6981.52, 2601)]
    monkeypatch.setattr("core.db.get_conn", _fake_get_conn(rows))
    df = query_ohlcv("AAM", 2)
    assert df["close"].tolist() == pytest.approx([6981.52, 7250.0])


def test_query_ohlcv_index_stays_in_points(monkeypatch):
    from datetime import date
    from tools.ohlcv_db import query_ohlcv
    rows = [(date(2026, 10, 9), 1737.63, 1744.89, 1724.17, 1735.09, 911324549)]
    monkeypatch.setattr("core.db.get_conn", _fake_get_conn(rows))
    assert query_ohlcv("VNINDEX", 1)["close"].iloc[-1] == pytest.approx(1735.09)
