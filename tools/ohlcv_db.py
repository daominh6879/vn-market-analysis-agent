"""
tools/ohlcv_db.py — Query layer for ohlcv_daily table (bài 23).

All functions return None/empty on any DB error — callers fall back to live API.
Never raise — tools must stay non-fatal.
"""

from __future__ import annotations

import sys
from typing import Optional

import pandas as pd


# ohlcv_daily stores stock prices in nghìn đồng (VCB 56.7 = 56,700 VND); market indices
# (VNINDEX...) are stored in points. Every reader here converts stock prices to VND so
# tool output ("... VND", traded value in tỷ) is in one unit. SQL that multiplies
# close * volume gets nghìn đồng — hence the * 1000 below.
INDEX_CODES = frozenset({"VNINDEX", "VN30", "HNX", "HNX30", "UPCOM"})
THOUSAND_VND = 1000.0


def _get_conn():
    from core.db import get_conn
    return get_conn()


def query_ohlcv(ticker: str, days: int) -> Optional[pd.DataFrame]:
    """Return last `days` rows for ticker from ohlcv_daily, or None on error."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT date, open, high, low, close, volume
                    FROM ohlcv_daily
                    WHERE ticker = %s
                    ORDER BY date DESC
                    LIMIT %s
                    """,
                    (ticker, days + 1),   # +1 for prev-close baseline
                )
                rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        df = df.sort_values("time").reset_index(drop=True)
        df["time"] = df["time"].astype(str)
        # Coerce price columns to float (DB may return Decimal or str)
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        # Stocks are stored in nghìn đồng → VND. Row-wise so legacy rows already in VND
        # (>= 1000, pre-2025-09 backfill) are not scaled twice; indices stay in points.
        if ticker.upper() not in INDEX_CODES:
            small = df["close"] < 1000
            for col in ("open", "high", "low", "close"):
                df.loc[small, col] = df.loc[small, col] * THOUSAND_VND

        # Remove outlier rows: corrupt DB entries whose close deviates by >95% from median.
        # A single bad row (e.g., stored as 0.021 instead of 21,000) skews RSI and Fibonacci.
        median_close = df["close"].median()
        if pd.notna(median_close) and median_close > 0:
            price_mask = df["close"].between(median_close * 0.05, median_close * 20.0)
            if price_mask.sum() >= 20:
                df = df[price_mask].reset_index(drop=True)

        return df
    except Exception as e:
        sys.stderr.write(f"[ohlcv_db] query_ohlcv({ticker}, {days}) failed: {e}\n")
        return None


def query_vn30_latest(tickers: list[str], sessions: int = 1) -> Optional[pd.DataFrame]:
    """
    Return latest close + close `sessions` back for each ticker in list.
    sessions=1 → day (prev close), 5 → week, 22 → month.
    Result has columns: ticker, date, close (VND), prev_close (VND), pct_change.
    Returns None on error.
    """
    if not tickers:
        return None
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Get latest (sessions+1) dates available in the table
                cur.execute(
                    """
                    SELECT DISTINCT date FROM ohlcv_daily
                    WHERE ticker = ANY(%s)
                    ORDER BY date DESC
                    LIMIT %s
                    """,
                    (tickers, sessions + 1),
                )
                dates = [row[0] for row in cur.fetchall()]

        if len(dates) < sessions + 1:
            return None

        latest_date, prev_date = dates[0], dates[-1]

        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        curr.ticker,
                        curr.date,
                        curr.close,
                        prev.close AS prev_close
                    FROM ohlcv_daily curr
                    JOIN ohlcv_daily prev
                        ON curr.ticker = prev.ticker AND prev.date = %s
                    WHERE curr.date = %s
                      AND curr.ticker = ANY(%s)
                    ORDER BY curr.ticker
                    """,
                    (prev_date, latest_date, tickers),
                )
                rows = cur.fetchall()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["ticker", "date", "close", "prev_close"])
        df["close"] = df["close"].astype(float) * THOUSAND_VND
        df["prev_close"] = df["prev_close"].astype(float) * THOUSAND_VND
        df["pct_change"] = ((df["close"] - df["prev_close"]) / df["prev_close"] * 100).round(2)
        return df
    except Exception as e:
        sys.stderr.write(f"[ohlcv_db] query_vn30_latest failed: {e}\n")
        return None


# query_universe_latest is the same logic — works for any ticker list (VN30 or HOSE).
query_universe_latest = query_vn30_latest


def query_top_by_value(tickers: list[str], limit: int = 10) -> Optional[pd.DataFrame]:
    """
    Return top `limit` tickers by close*volume (proxy for traded value, VND) from latest session.
    Columns: ticker, date, close (VND), volume, traded_value (VND), pct_change.
    Returns None on error.
    """
    if not tickers:
        return None
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT date FROM ohlcv_daily
                    WHERE ticker = ANY(%s)
                    ORDER BY date DESC
                    LIMIT 2
                    """,
                    (tickers,),
                )
                dates = [row[0] for row in cur.fetchall()]

        if len(dates) < 2:
            return None

        latest_date, prev_date = dates[0], dates[1]

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        curr.ticker,
                        curr.date,
                        curr.close,
                        curr.volume,
                        curr.close * curr.volume * 1000 AS traded_value,  -- nghìn đồng → VND
                        prev.close AS prev_close
                    FROM ohlcv_daily curr
                    JOIN ohlcv_daily prev
                        ON curr.ticker = prev.ticker AND prev.date = %s
                    WHERE curr.date = %s
                      AND curr.ticker = ANY(%s)
                    ORDER BY traded_value DESC
                    LIMIT %s
                    """,
                    (prev_date, latest_date, tickers, limit),
                )
                rows = cur.fetchall()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["ticker", "date", "close", "volume",
                                          "traded_value", "prev_close"])
        for col in ["close", "volume", "traded_value", "prev_close"]:
            df[col] = df[col].astype(float)
        df["close"] = df["close"] * THOUSAND_VND
        df["prev_close"] = df["prev_close"] * THOUSAND_VND
        df["pct_change"] = ((df["close"] - df["prev_close"]) / df["prev_close"] * 100).round(2)
        return df
    except Exception as e:
        sys.stderr.write(f"[ohlcv_db] query_top_by_value failed: {e}\n")
        return None
