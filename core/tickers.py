"""
core/tickers.py — Runtime ticker list from securities table.

Primary: SELECT ticker FROM securities WHERE is_active = true ORDER BY ticker
Fallback: TICKERS env var (comma-separated), then ["HPG"]
"""
from __future__ import annotations

import os

# Fallback hardcoded when securities table is unavailable
_RATIO_FALLBACK = [
    "VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "STB",
    "HPG", "HSG", "NKG", "TLH",
    "FPT", "CMG", "VGI",
    "VHM", "VIC", "NVL", "PDR", "DXG",
]

_SECTOR_FALLBACK: dict[str, list[str]] = {
    t: ["VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "STB"]
    for t in ["VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "STB"]
} | {
    t: ["HPG", "HSG", "NKG", "TLH"]
    for t in ["HPG", "HSG", "NKG", "TLH"]
} | {
    t: ["FPT", "CMG", "VGI"]
    for t in ["FPT", "CMG", "VGI"]
} | {
    t: ["VHM", "VIC", "NVL", "PDR", "DXG"]
    for t in ["VHM", "VIC", "NVL", "PDR", "DXG"]
}


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


def get_ratio_tickers() -> list[str]:
    """Return VN30+VN100 active tickers for daily ratio pre-fetch. Falls back to hardcoded list."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker FROM securities
                    WHERE is_active = true
                      AND (index_member @> ARRAY['VN30'] OR index_member @> ARRAY['VN100'])
                    ORDER BY
                      CASE WHEN index_member @> ARRAY['VN30'] THEN 0 ELSE 1 END,
                      ticker
                    """
                )
                rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except Exception:
        pass
    return list(_RATIO_FALLBACK)


def get_sector_peers(ticker: str, max_peers: int = 10) -> list[str]:
    """Return active VN30/VN100 tickers in the same sector. Falls back to hardcoded map."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sector FROM securities WHERE ticker = %s AND is_active = true",
                    (ticker,),
                )
                row = cur.fetchone()
        if not row or row[0] in ("Unknown", ""):
            return _SECTOR_FALLBACK.get(ticker, [ticker])
        sector = row[0]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker FROM securities
                    WHERE sector = %s AND is_active = true
                      AND (index_member @> ARRAY['VN30'] OR index_member @> ARRAY['VN100'])
                    ORDER BY
                      CASE WHEN index_member @> ARRAY['VN30'] THEN 0 ELSE 1 END,
                      ticker
                    LIMIT %s
                    """,
                    (sector, max_peers),
                )
                rows = cur.fetchall()
        peers = [r[0] for r in rows]
        if not peers:
            return _SECTOR_FALLBACK.get(ticker, [ticker])
        # Ensure query ticker is included
        if ticker not in peers:
            peers = [ticker] + peers[: max_peers - 1]
        return peers
    except Exception:
        return _SECTOR_FALLBACK.get(ticker, [ticker])
