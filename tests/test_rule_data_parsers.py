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


def test_web_facts_checks_keep_only_quoted_dated_facts():
    from datetime import datetime, timezone
    from ingest.web_facts import check_interbank, check_review
    body = "Kết tuần, lãi suất kỳ hạn qua đêm là 6,6% (tăng 163 điểm cơ bản). NHNN bơm ròng 37 nghìn tỷ."
    pub = datetime(2022, 10, 11, 2, 14, tzinfo=timezone.utc)
    facts = [{"date": "2022-10-07", "tenor": "overnight", "rate_pct": 6.6, "quote": "Kết tuần, lãi suất kỳ hạn qua đêm là 6,6%"},
             {"date": "2022-10-07", "tenor": "1w", "rate_pct": 7.2, "quote": "Kết tuần, lãi suất kỳ hạn qua đêm là 6,6%"},
             {"date": "2022-09-01", "tenor": "overnight", "rate_pct": 6.6, "quote": "Kết tuần, lãi suất kỳ hạn qua đêm là 6,6%"},
             {"date": "2022-10-07", "tenor": "overnight", "rate_pct": 6.6, "quote": "lãi suất qua đêm 6,6% theo bài khác"}]
    assert check_interbank(facts, body, pub) == [("interbank_on", datetime(2022, 10, 7).date(), 6.6,
                                                  "Kết tuần, lãi suất kỳ hạn qua đêm là 6,6%")]
    body = "Tại rổ VNDiamond, TCM bị loại khỏi chỉ số trong khi NLG được thêm vào chỉ số kỳ này."
    ch = [{"index": "VNDiamond", "ticker": "NLG", "action": "add", "effective_date": "2022-11-07",
           "quote": "TCM bị loại khỏi chỉ số trong khi NLG được thêm vào chỉ số kỳ này"},
          {"index": "VN30", "ticker": "XXX", "action": "add", "effective_date": "2022-11-07",
           "quote": "TCM bị loại khỏi chỉ số trong khi NLG được thêm vào chỉ số kỳ này"}]
    assert check_review(ch, body, datetime(2022, 10, 21, 10, tzinfo=timezone.utc), {"NLG", "TCM"}) == [
        ("VNDIAMOND", "NLG", "add", datetime(2022, 11, 7).date(), ch[0]["quote"])]


def test_sbv_rate_rows_store_as_seen_and_date_starred_tenors_to_reference():
    from zoneinfo import ZoneInfo
    from ingest.fetch_rule_data import sbv_rate_rows
    now = datetime(2026, 10, 31, 10, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    ib = pd.DataFrame([{"date": pd.Timestamp(2026, 10, 29), "tenor": "on", "rate": 3.74, "volume": 809175.0,
                        "reference_date": pd.Timestamp(2026, 10, 29)},
                       {"date": pd.Timestamp(2026, 10, 29), "tenor": "9m", "rate": 7.11, "volume": 100.0,
                        "reference_date": pd.Timestamp(2026, 10, 27)}])
    pol = pd.DataFrame([{"kind": "refinancing", "name": "Lãi suất tái cấp vốn", "rate": 4.5,
                         "decision": "1123/QĐ-NHNN ngày 16/06/2023", "decision_date": pd.Timestamp(2023, 6, 16),
                         "effective_date": pd.Timestamp(2023, 3, 19)}])
    omo = pd.DataFrame([{"date": pd.Timestamp(2026, 10, 30), "type": "repo", "tenor_days": 7, "members": 11,
                         "winners": 11, "volume": 23598.36, "rate": 4.5},
                        {"date": pd.Timestamp(2026, 10, 30), "type": "repo", "tenor_days": 0, "members": None,
                         "winners": None, "volume": 29935.76, "rate": None}])
    rows = {(r[0], r[1]): r for r in sbv_rate_rows({"interbank": ib, "policy": pol}, omo, now)}
    assert rows[("interbank_on", date(2026, 10, 29))][2:5] == (3.74, now, "sbv")
    assert ("interbank_9m", date(2026, 10, 27)) in rows
    assert rows[("interbank_volume_on", date(2026, 10, 29))][2] == 809175.0
    ref = rows[("sbv_refinancing_rate", date(2023, 3, 19))]
    assert ref[2] == 4.5 and ref[3].date() == date(2023, 6, 16)          # known from the decision, not before
    assert rows[("omo_repo_7d_rate", date(2026, 10, 30))][2] == 4.5
    assert rows[("omo_repo_volume", date(2026, 10, 30))][2] == 29935.76
    assert sbv_rate_rows(None, None, now) == []


def test_sbv_cpi_month():
    from ingest.fetch_rule_data import sbv_cpi_month
    assert sbv_cpi_month("CPI tháng 9/2026 tăng 0,62% so với tháng trước", date(2026, 10, 5)) == (2026, 9)
    assert sbv_cpi_month("CPI tháng 01/2015 giảm 0,2% so với tháng trước", date(2015, 3, 3)) == (2015, 1)
    assert sbv_cpi_month("CPI tháng 2 giảm 0,05%", date(2015, 3, 3)) == (2015, 2)
    assert sbv_cpi_month("Lạm phát cơ bản tám tháng đầu năm tăng 2,71%", date(2024, 9, 9)) is None
    assert sbv_cpi_month("CPI bình quân năm sau so với năm trước (từ 2005-2014)", date(2015, 1, 31)) is None
