"""
ingest/seed_securities.py — Seed/refresh securities table from hose_universe data.

Reads HOSE_SEED (always available) or hose_tickers.json (if fetch_and_save was run).
Upserts into securities table — safe to re-run; uses ON CONFLICT DO UPDATE.

Usage:
    python ingest/seed_securities.py           # seed from HOSE_SEED (~180 tickers)
    python ingest/seed_securities.py --refresh  # fetch live VCI list first, then seed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def upsert_securities(rows: list[dict]) -> int:
    """Upsert rows into securities table. Returns count inserted/updated."""
    if not rows:
        return 0
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO securities
                        (ticker, exchange, sector, index_member, is_active)
                    VALUES
                        (%(ticker)s, %(exchange)s, %(sector)s, %(index_member)s, %(is_active)s)
                    ON CONFLICT (ticker) DO UPDATE SET
                        sector       = EXCLUDED.sector,
                        exchange     = EXCLUDED.exchange,
                        index_member = EXCLUDED.index_member,
                        is_active    = EXCLUDED.is_active
                    """,
                    rows,
                )
            conn.commit()
        return len(rows)
    except Exception as e:
        sys.stderr.write(f"[seed_securities] upsert failed: {e}\n")
        return 0


def seed_from_hose_seed() -> int:
    """Seed securities from HOSE_SEED hardcoded list."""
    from data.hose_universe import HOSE_SEED

    rows = [
        {
            "ticker": ticker,
            "exchange": "HOSE",
            "sector": sector,
            "index_member": [index_member],  # TEXT[] in Postgres
            "is_active": True,
        }
        for ticker, sector, index_member in HOSE_SEED
    ]
    return upsert_securities(rows)


def seed_from_json() -> int:
    """Seed securities from hose_tickers.json (richer, from VCI fetch)."""
    from data.hose_universe import load_hose_universe

    universe = load_hose_universe()  # [{ticker, sector, index_member}]
    if not universe:
        sys.stderr.write("[seed_securities] load_hose_universe returned empty — falling back to HOSE_SEED\n")
        return seed_from_hose_seed()

    rows = [
        {
            "ticker": u["ticker"],
            "exchange": "HOSE",
            "sector": u.get("sector") or "Không phân loại",
            "index_member": [u["index_member"]] if u.get("index_member") else [],
            "is_active": True,
        }
        for u in universe
    ]
    return upsert_securities(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed securities table from hose_universe")
    parser.add_argument("--refresh", action="store_true",
                        help="Fetch live VCI ticker list first, then seed (calls fetch_and_save_hose_universe)")
    args = parser.parse_args()

    if args.refresh:
        print("Fetching live VCI universe …")
        try:
            from data.hose_universe import fetch_and_save_hose_universe
            count = fetch_and_save_hose_universe()
            print(f"  VCI returned {count} tickers → saved to hose_tickers.json")
        except Exception as e:
            sys.stderr.write(f"  VCI fetch failed ({e}) — using HOSE_SEED fallback\n")

    print("Seeding securities table …")
    n = seed_from_json()
    if n:
        print(f"Done — {n} rows upserted into securities")
    else:
        print("0 rows upserted — check stderr for errors")


if __name__ == "__main__":
    main()
