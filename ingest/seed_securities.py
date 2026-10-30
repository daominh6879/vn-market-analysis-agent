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
                        (ticker, exchange, sector, industry, company_name,
                         name_aliases, tags, index_member, is_active)
                    VALUES
                        (%(ticker)s, %(exchange)s, %(sector)s, %(industry)s, %(company_name)s,
                         %(name_aliases)s, %(tags)s, %(index_member)s, %(is_active)s)
                    ON CONFLICT (ticker) DO UPDATE SET
                        sector       = EXCLUDED.sector,
                        exchange     = EXCLUDED.exchange,
                        industry     = EXCLUDED.industry,
                        company_name = EXCLUDED.company_name,
                        name_aliases = EXCLUDED.name_aliases,
                        tags         = EXCLUDED.tags,
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


LISTING_EXCHANGE = {"HSX": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM"}   # vnmarket listing (VCI spelling) → our label


def exchange_updates(current: dict[str, str], listing: dict[str, str]) -> list[tuple[str, str]]:
    """(exchange, ticker) for every active security whose label differs from where the stock is listed now.
    Pure. A ticker the listing does not know keeps its label (delisted / suspended rows are not guessed)."""
    out = []
    for t, ex in current.items():
        real = LISTING_EXCHANGE.get(listing.get(t, ""))
        if real and real != ex:
            out.append((real, t))
    return sorted(out, key=lambda x: x[1])


def delisted(current: dict[str, str], listing: dict[str, str]) -> list[str]:
    """Active securities the listing reports DELISTED. Pure."""
    return sorted(t for t in current if listing.get(t) == "DELISTED")


def sync_exchanges() -> list[tuple[str, str]]:
    """Set securities.exchange of active rows from the live listing. Until 30/10/2026 every row was written 'HOSE'
    (HOSE_SEED carried HNX / UPCoM stocks: PVS, APS, CEO, ACV, ABB…), so the rule engine gave them the ±7% band.
    A stock the listing reports DELISTED (BCG, SVI on 30/10/2026, still active here) is set inactive, like the other
    former HOSE rows. Returns the (exchange or "DELISTED", ticker) changes; raises if the listing is empty (a run that
    checks nothing must fail)."""
    from core.db import get_conn
    from core.vnmarket_client import get_client

    df = get_client().listing.all()
    listing = dict(zip(df["symbol"].astype(str), df["exchange"].astype(str)))
    if not listing:
        raise RuntimeError("vnmarket listing.all() returned nothing — exchanges not checked")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, exchange FROM securities WHERE is_active")
            current = dict(cur.fetchall())
            changes = exchange_updates(current, listing)
            if changes:
                cur.executemany("UPDATE securities SET exchange = %s, updated_at = NOW() WHERE ticker = %s", changes)
            gone = delisted(current, listing)
            if gone:
                cur.execute("UPDATE securities SET is_active = false, updated_at = NOW() WHERE ticker = ANY(%s)", (gone,))
        conn.commit()
    return changes + [("DELISTED", t) for t in gone]


def seed_from_hose_seed() -> int:
    """Seed securities from HOSE_SEED hardcoded list (company_name left empty — no vnstock)."""
    from data.hose_universe import HOSE_SEED, _enrich

    rows = []
    for ticker, sector, index_member in HOSE_SEED:
        enr = _enrich(ticker, sector)
        rows.append({
            "ticker": ticker,
            "exchange": "HOSE",
            "sector": sector,
            "industry": enr["industry"],
            "company_name": "",
            "name_aliases": enr["name_aliases"],
            "tags": enr["tags"],
            "index_member": [index_member],  # TEXT[] in Postgres
            "is_active": True,
        })
    return upsert_securities(rows)


def seed_from_json() -> int:
    """Seed securities from hose_tickers.json (richer, from VCI fetch)."""
    from data.hose_universe import load_hose_universe

    universe = load_hose_universe()  # full records incl. company_name/industry/aliases/tags
    if not universe:
        sys.stderr.write("[seed_securities] load_hose_universe returned empty — falling back to HOSE_SEED\n")
        return seed_from_hose_seed()

    rows = [
        {
            "ticker": u["ticker"],
            "exchange": "HOSE",
            "sector": u.get("sector") or "Không phân loại",
            "industry": u.get("industry") or "Unknown",
            "company_name": u.get("company_name") or "",
            "name_aliases": u.get("name_aliases") or [],
            "tags": u.get("tags") or [],
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
    moved = sync_exchanges()
    print(f"Exchange labels fixed from the listing: {len(moved)} {moved}")


if __name__ == "__main__":
    main()
