"""
signals/breakout_live.py — log today's breakout events (with the proprietary-flow gate) to breakout_signals.

Forward test of the finding in docs/notes/portfolio-signals.md: MID breakouts where proprietary desks net-bought
> PROP_GATE of traded value over the last 5 sessions did better than ungated ones in backtests. Events of the latest
session are written; gated and ungated are both kept so signals/breakout_monitor.py can compare realized outcomes.

Usage (the Dagster asset breakout_signals_daily calls score_latest):
    python -m signals.breakout_live
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from signals.breakout_eval import MIN_VALUE_BN, events, load, signals

PROP_GATE = 0.01
TYPES = ("SHORT", "MID", "LONG")


def prop_ratio(wide: dict[str, pd.DataFrame], window: int) -> pd.DataFrame:
    """Proprietary net value over `window` sessions / traded value over the same sessions (both tỷ đồng)."""
    from core.db import get_conn
    C, V = wide["close"], wide["volume"]
    with get_conn() as conn:
        p = pd.read_sql_query("SELECT ticker, date, net_value FROM proprietary_flows", conn)
    p["date"] = pd.to_datetime(p["date"])
    net = p.pivot_table(index="date", columns="ticker", values="net_value", aggfunc="last").astype(float)
    net = net.reindex(index=C.index, columns=C.columns).fillna(0.0)
    return net.rolling(window).sum() / (C * V / 1e6).rolling(window).sum()


def build_rows(wide: dict, index: pd.Series) -> list[tuple]:
    """Event rows for the latest session in `wide` (liquid names only)."""
    C = wide["close"]
    value_bn = (C * wide["volume"] / 1e6).rolling(20, min_periods=15).mean()
    liquid = value_bn >= MIN_VALUE_BN
    r5, r20 = prop_ratio(wide, 5), prop_ratio(wide, 20)
    sg = signals(wide, index, "loose")
    day = C.index[-1]
    rows = []
    for kind in TYPES:
        hit = (events(sg[kind]) & liquid).iloc[-1]
        for t in hit.index[hit.to_numpy()]:
            a5, a20 = r5.at[day, t], r20.at[day, t]
            rows.append((t, day.date(), kind, float(C.at[day, t]), float(value_bn.at[day, t]),
                         None if np.isnan(a5) else float(a5), None if np.isnan(a20) else float(a20),
                         bool(a5 > PROP_GATE) if not np.isnan(a5) else False))
    return rows


def score_latest() -> dict:
    """Write the latest session's events. Raises when prices or proprietary flows are not up to date for that session."""
    from core.db import get_conn
    wide, index = load()
    day = wide["close"].index[-1]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM ohlcv_daily")
        last_price = cur.fetchone()[0]
        cur.execute("SELECT MAX(date) FROM proprietary_flows")
        last_prop = cur.fetchone()[0]
    if last_prop is None or last_prop < day.date() - timedelta(days=4):
        raise RuntimeError(f"proprietary_flows is stale (latest {last_prop}, prices {last_price}); gate would be wrong")
    rows = build_rows(wide, index)
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO breakout_signals (ticker, date, signal_type, close, avg_value_bn, prop_ratio_5d, prop_ratio_20d, prop_gate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date, signal_type) DO UPDATE
                SET close=EXCLUDED.close, avg_value_bn=EXCLUDED.avg_value_bn, prop_ratio_5d=EXCLUDED.prop_ratio_5d,
                    prop_ratio_20d=EXCLUDED.prop_ratio_20d, prop_gate=EXCLUDED.prop_gate
            """, rows)
    gated = sum(1 for r in rows if r[2] == "MID" and r[7])
    return {"date": str(day.date()), "events": len(rows), "mid": sum(1 for r in rows if r[2] == "MID"), "mid_gated": gated}


if __name__ == "__main__":
    print(score_latest())
