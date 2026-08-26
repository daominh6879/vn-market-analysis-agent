"""
tools/index_db.py — Query layer for market_index_daily table.

All functions return None on DB error — callers fall back to live API.
Never raise.
"""

from __future__ import annotations

import sys
from typing import Optional

import pandas as pd


def query_index(index_code: str, days: int) -> Optional[pd.DataFrame]:
    """Return last `days` rows for index_code from market_index_daily, or None on error."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, open, high, low, close,
                           change_pts, change_pct, matched_value, matched_volume, foreign_net
                    FROM market_index_daily
                    WHERE index_code = %s
                    ORDER BY date DESC
                    LIMIT %s
                    """,
                    (index_code.upper(), days + 1),
                )
                rows = cur.fetchall()
        if not rows:
            return None
        cols = ["time", "open", "high", "low", "close",
                "change_pts", "change_pct", "matched_value", "matched_volume", "foreign_net"]
        df = pd.DataFrame(rows, columns=cols)
        df = df.sort_values("time").reset_index(drop=True)
        df["time"] = df["time"].astype(str)
        return df
    except Exception as e:
        sys.stderr.write(f"[index_db] query_index({index_code}, {days}) failed: {e}\n")
        return None


def query_index_latest(index_code: str) -> Optional[dict]:
    """Return most recent row as dict, or None."""
    df = query_index(index_code, days=1)
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    return {
        "index_code": index_code.upper(),
        "date": str(row["time"]),
        "close": float(row["close"]),
        "change_pts": float(row["change_pts"]),
        "change_pct": float(row["change_pct"]),
        "matched_value": float(row["matched_value"]),
        "matched_volume": int(row["matched_volume"]),
        "foreign_net": float(row["foreign_net"]),
    }


def upsert_index_rows(rows: list[dict]) -> int:
    """Upsert rows into market_index_daily. Returns count inserted/updated."""
    if not rows:
        return 0
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO market_index_daily
                        (index_code, date, open, high, low, close,
                         change_pts, change_pct, matched_volume, matched_value, foreign_net)
                    VALUES
                        (%(index_code)s, %(date)s, %(open)s, %(high)s, %(low)s, %(close)s,
                         %(change_pts)s, %(change_pct)s, %(matched_volume)s,
                         %(matched_value)s, %(foreign_net)s)
                    ON CONFLICT (index_code, date) DO UPDATE SET
                        open          = EXCLUDED.open,
                        high          = EXCLUDED.high,
                        low           = EXCLUDED.low,
                        close         = EXCLUDED.close,
                        change_pts    = EXCLUDED.change_pts,
                        change_pct    = EXCLUDED.change_pct,
                        matched_volume = EXCLUDED.matched_volume,
                        matched_value = EXCLUDED.matched_value,
                        foreign_net   = EXCLUDED.foreign_net,
                        fetched_at    = NOW()
                    """,
                    rows,
                )
            conn.commit()
        return len(rows)
    except Exception as e:
        sys.stderr.write(f"[index_db] upsert_index_rows failed: {e}\n")
        return 0
