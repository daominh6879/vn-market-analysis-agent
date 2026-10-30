"""Pure parsers of ingest/fetch_rule_data.py: VBMA short-term rates (point in time) and the 1-minute → 14:25 snapshot."""
from datetime import date, datetime

import pandas as pd

from ingest.fetch_rule_data import intraday_rows, vbma_rows


def test_vbma_rows_usable_from_the_next_morning():
    df = pd.DataFrame({"date": pd.to_datetime(["2026-10-28", "2026-10-29"]), "2w": [4.092, 4.2], "3w": [None, 4.215],
                       "1m": [4.167, 4.229], "2m": [4.425] * 2, "3m": [4.525] * 2, "6m": [4.771] * 2,
                       "9m": [4.821] * 2, "1y": [4.846] * 2})
    rows = vbma_rows(df, since=date(2026, 10, 29))
    assert {r[1] for r in rows} == {date(2026, 10, 29)} and len(rows) == 8
    r = next(x for x in rows if x[0] == "stb_1m")
    assert r[2] == 4.229 and r[4] == "vbma"
    assert r[3].isoformat() == "2026-10-30T09:00:00+07:00"           # never the session of the fixing itself
    assert not [x for x in vbma_rows(df, None) if x[1] == date(2026, 10, 28) and x[0] == "stb_3w"]


def test_intraday_rows_snapshot_before_1425_and_atc():
    t = pd.to_datetime(["2026-10-29 09:15", "2026-10-29 14:24", "2026-10-29 14:25", "2026-10-29 14:45"])
    bars = pd.DataFrame({"time": t, "open": [54500, 53000, 53100, 52700], "high": [54800, 53200, 53300, 52700],
                         "low": [54500, 52900, 53000, 52700], "close": [54700, 53100, 53000, 52700],
                         "volume": [223900, 1000, 2000, 413200]})
    (row,) = intraday_rows("FPT", bars, "vci")
    assert row[:8] == ("FPT", datetime(2026, 10, 29).date(), 54500.0, 54800.0, 52900.0, 53100.0, 224900, 52700.0)
    assert row[8:] == (413200, 52700.0, 640100, "vci")


def test_parse_cpi_month_only_not_period_averages():
    from ingest.fetch_rule_data import parse_cpi
    text = ("CPI tháng Năm tăng 0,16% so với tháng trước, tăng 3,82% so với tháng 12/2025 và tăng 5,60% so với cùng kỳ "
            "năm trước. Tính chung năm tháng đầu năm 2026, CPI tăng 4,31% so với cùng kỳ năm 2025. Bên cạnh đó, yếu tố "
            "góp phần kiềm chế tốc độ tăng CPI sáu tháng giảm 1,41% so với cùng kỳ năm trước. Giá gas làm CPI chung tháng "
            "này tăng 21,1% so với cùng kỳ năm trước.")
    out = parse_cpi(text)
    assert out["cpi_yoy"][0] == 5.60 and out["cpi_mom"][0] == 0.16 and out["cpi_ytd"][0] == 3.82
    assert parse_cpi("Chỉ số giá tiêu dùng (CPI) tháng 3/2019 giảm 0,21% so với tháng trước.")["cpi_mom"][0] == -0.21
    assert "cpi_yoy" not in parse_cpi("Tại Mỹ, CPI tháng 10/2021 tăng 6,2% so với cùng kỳ năm trước.")
