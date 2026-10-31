"""portfolio/market_data._fireant_history: FireAnt fills whole missing periods and single missing fields, never a
vnstock value nor a code whose vn-portfolio alias vnstock already has (no DB)."""
from __future__ import annotations

from collections import defaultdict

from portfolio import market_data as M


def _fin(periods: dict) -> dict:
    out = defaultdict(lambda: defaultdict(dict))
    for t, ps in periods.items():
        for p, v in ps.items():
            out[t][p] = dict(v)
    return out


def test_fills_missing_fields_and_periods_without_overwriting():
    out = _fin({"AAA": {"2024": {"doanh_thu_thuan": 100.0, "co_ong_cua_cong_ty_me": 9.0},
                        "2025": {"doanh_thu_thuan": 110.0}}})
    fa = [("AAA", 2024, 0, "CashflowFromOperatingActivity", 7), ("AAA", 2024, 0, "ParentCompanyShareholderProfitAfterTax", 8),
          ("AAA", 2024, 0, "NetSale", 999), ("AAA", 2023, 0, "NetSale", 90),
          ("AAA", 2023, 0, "CashflowFromOperatingActivity", 5), ("AAA", 2025, 0, "Cash", 3),
          ("AAA", 2025, 0, "CashEquivalent", 2), ("AAA", 2026, 2, "PaidInCapital", 1000)]
    r = M._fireant_history(out, fa, None)
    a = out["AAA"]
    assert a["2024"]["dong_tien_hoat_dong"] == 7                       # missing OCF filled
    assert a["2024"]["doanh_thu_thuan"] == 100.0                       # vnstock value kept
    assert "loi_nhuan_cua_co_ong_cua_cong_ty_me" not in a["2024"]      # vnstock alias present → not shadowed
    assert a["2023"] == {"doanh_thu_thuan": 90.0, "dong_tien_hoat_dong": 5.0}
    assert a["2025"]["tien_va_tuong_duong_tien"] == 5
    assert a["Q2/2026"] == {"von_dieu_le": 1000.0}
    assert r == {"periods": 2, "fields": 2}


def test_metrics_filter_applies_to_filled_fields():
    out = _fin({"AAA": {"2024": {"doanh_thu_thuan": 100.0}}})
    M._fireant_history(out, [("AAA", 2024, 0, "CashflowFromOperatingActivity", 7),
                             ("AAA", 2024, 0, "TotalAsset", 50)], ["doanh_thu_thuan", "tong_tai_san"])
    assert out["AAA"]["2024"] == {"doanh_thu_thuan": 100.0, "tong_tai_san": 50.0}
