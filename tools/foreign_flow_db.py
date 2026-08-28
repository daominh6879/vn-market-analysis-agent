"""
tools/foreign_flow_db.py — Query layer for foreign_flows table (Phase 2).

All functions return None on DB error — callers handle gracefully.
Never raise.
"""

from __future__ import annotations

import sys
from datetime import date as date_type
from typing import Optional

import pandas as pd


def query_market_foreign_net(target_date: date_type) -> Optional[dict]:
    """Return market-level foreign net for target_date, or None on error."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        SUM(buy_value)  AS total_buy,
                        SUM(sell_value) AS total_sell,
                        SUM(net_value)  AS net
                    FROM foreign_flows
                    WHERE date = %s
                    """,
                    (target_date,),
                )
                row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return {
            "date": str(target_date),
            "total_buy": float(row[0]),
            "total_sell": float(row[1]),
            "net_value": float(row[2]),
        }
    except Exception as e:
        sys.stderr.write(f"[foreign_flow_db] query_market_foreign_net({target_date}) failed: {e}\n")
        return None


def query_top_foreign(
    target_date: date_type,
    n: int = 5,
    direction: str = "buy",
) -> Optional[list[dict]]:
    """
    Return top n tickers sorted by direction on target_date.
    direction: "buy" → top by buy_value DESC
               "sell" → top by sell_value DESC
               "net_buy" → top net buyers (net_value DESC)
               "net_sell" → top net sellers (net_value ASC)
    Returns None on error.
    """
    order_col = {
        "buy": "buy_value DESC",
        "sell": "sell_value DESC",
        "net_buy": "net_value DESC",
        "net_sell": "net_value ASC",
    }.get(direction, "buy_value DESC")

    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ticker, buy_value, sell_value, net_value,
                           buy_volume, sell_volume, net_volume
                    FROM foreign_flows
                    WHERE date = %s
                    ORDER BY {order_col}
                    LIMIT %s
                    """,
                    (target_date, n),
                )
                rows = cur.fetchall()
        if not rows:
            return None
        cols = ["ticker", "buy_value", "sell_value", "net_value",
                "buy_volume", "sell_volume", "net_volume"]
        df = pd.DataFrame(rows, columns=cols)
        for col in ["buy_value", "sell_value", "net_value"]:
            df[col] = df[col].astype(float)
        return df.to_dict("records")
    except Exception as e:
        sys.stderr.write(f"[foreign_flow_db] query_top_foreign({target_date}) failed: {e}\n")
        return None


def query_latest_foreign_date(as_of_date: Optional[str] = None) -> Optional[date_type]:
    """Return most recent date in foreign_flows <= as_of_date (or None if table empty).

    as_of_date: ISO string 'YYYY-MM-DD'. If None, returns absolute MAX(date).
    """
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                if as_of_date:
                    cur.execute(
                        "SELECT MAX(date) FROM foreign_flows WHERE date <= %s",
                        (as_of_date,),
                    )
                else:
                    cur.execute("SELECT MAX(date) FROM foreign_flows")
                row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as e:
        sys.stderr.write(f"[foreign_flow_db] query_latest_foreign_date failed: {e}\n")
        return None


def query_foreign_net_streak(as_of_date: Optional[str] = None) -> Optional[dict]:
    """Count consecutive days of market-level net buying/selling ending at as_of_date.

    Returns {"streak": int, "direction": "buy"|"sell", "latest_date": str} or None.
    Queries up to 10 recent dates to keep it cheap.
    """
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                if as_of_date:
                    cur.execute(
                        """
                        SELECT date, SUM(net_value) AS net
                        FROM foreign_flows
                        WHERE date <= %s
                        GROUP BY date
                        ORDER BY date DESC
                        LIMIT 10
                        """,
                        (as_of_date,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT date, SUM(net_value) AS net
                        FROM foreign_flows
                        GROUP BY date
                        ORDER BY date DESC
                        LIMIT 10
                        """
                    )
                rows = cur.fetchall()
        if not rows:
            return None
        nets = [(str(r[0]), float(r[1] or 0)) for r in rows]
        latest_net = nets[0][1]
        if latest_net == 0:
            return {"streak": 0, "direction": "neutral", "latest_date": nets[0][0]}
        direction = "buy" if latest_net > 0 else "sell"
        streak = 0
        for _d, net in nets:
            if (direction == "buy" and net > 0) or (direction == "sell" and net < 0):
                streak += 1
            else:
                break
        return {"streak": streak, "direction": direction, "latest_date": nets[0][0]}
    except Exception as e:
        sys.stderr.write(f"[foreign_flow_db] query_foreign_net_streak failed: {e}\n")
        return None


def upsert_foreign_rows(rows: list[dict]) -> int:
    """Upsert rows into foreign_flows. Returns count inserted/updated."""
    if not rows:
        return 0
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO foreign_flows
                        (ticker, date, buy_value, sell_value, net_value,
                         buy_volume, sell_volume, net_volume)
                    VALUES
                        (%(ticker)s, %(date)s, %(buy_value)s, %(sell_value)s,
                         %(net_value)s, %(buy_volume)s, %(sell_volume)s, %(net_volume)s)
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        buy_value  = EXCLUDED.buy_value,
                        sell_value = EXCLUDED.sell_value,
                        net_value  = EXCLUDED.net_value,
                        buy_volume = EXCLUDED.buy_volume,
                        sell_volume = EXCLUDED.sell_volume,
                        net_volume  = EXCLUDED.net_volume
                    """,
                    rows,
                )
        return len(rows)
    except Exception as e:
        sys.stderr.write(f"[foreign_flow_db] upsert_foreign_rows failed: {e}\n")
        return 0
