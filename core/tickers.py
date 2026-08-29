"""
core/tickers.py — Runtime ticker list from securities table.

Primary: SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker
Fallback: TICKERS env var (comma-separated), then ["HPG"]
"""
from __future__ import annotations

import os


def get_tickers() -> list[str]:
    """Return active tickers from securities table. Falls back to TICKERS env var."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker"
                )
                rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass

    env = os.getenv("TICKERS", "HPG")
    return [t.strip().upper() for t in env.split(",") if t.strip()]
