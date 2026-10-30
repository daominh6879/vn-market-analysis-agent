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
