"""
ingest/fetch_foreign_flows.py — Fetch per-ticker foreign trading data via
VciDirectProvider.fetch_foreign_batch() and upsert into foreign_flows table.

Source: POST https://trading.vietcap.com.vn/api/price/symbols/getList
Same provider already used for OHLCV — no new dependency.

Usage:
    python ingest/fetch_foreign_flows.py               # today (live session data)
    python ingest/fetch_foreign_flows.py --date 2026-08-25  # date label in DB
    python ingest/fetch_foreign_flows.py --market HNX       # HNX instead of HOSE
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

_CHUNK_SIZE = 50  # safe batch size for VCI price board endpoint


def _parse_date(date_str: str | None) -> date:
    if not date_str:
        return date.today()
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def fetch_foreign_via_vci(target_date: date, market: str = "HOSE") -> list[dict]:
    """
    Fetch foreign buy/sell per ticker from VCI price board endpoint.

    Returns list of row dicts ready for upsert.
    Raises on error — caller handles.
    """
    from data.hose_universe import load_hose_tickers
    from tools.providers import VciDirectProvider

    if market.upper() == "HOSE":
        tickers = load_hose_tickers()
    else:
        tickers = ["SHB", "NVB", "ACB", "HUT", "PVS", "PVC", "BVS", "SHS"]

    if not tickers:
        raise ValueError("Empty ticker universe")

    provider = VciDirectProvider()
    date_str = str(target_date)
    rows: list[dict] = []

    for i in range(0, len(tickers), _CHUNK_SIZE):
        chunk = tickers[i : i + _CHUNK_SIZE]
        try:
            batch = provider.fetch_foreign_batch(chunk)
        except Exception as e:
            sys.stderr.write(f"[fetch_foreign_flows] chunk {i}-{i+len(chunk)} failed: {e}\n")
            continue
        for item in batch:
            rows.append({**item, "date": date_str})

    return rows


def fetch_and_upsert(target_date: date, market: str = "HOSE") -> int:
    """Fetch foreign flows and upsert to DB. Returns row count (0 on error)."""
    from tools.foreign_flow_db import upsert_foreign_rows

    try:
        rows = fetch_foreign_via_vci(target_date, market)
    except Exception as e:
        sys.stderr.write(f"[fetch_foreign_flows] fetch failed for {target_date}: {e}\n")
        return 0

    if not rows:
        sys.stderr.write(f"[fetch_foreign_flows] No records for {target_date} / {market}\n")
        return 0

    return upsert_foreign_rows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch foreign flows via VCI → Postgres")
    parser.add_argument("--date", default=None,
                        help="Date label for DB rows (YYYY-MM-DD). Default: today.")
    parser.add_argument("--market", default="HOSE", help="HOSE (default) or HNX.")
    args = parser.parse_args()

    target_date = _parse_date(args.date)
    print(f"Fetching foreign flows: {target_date} / {args.market}")
    n = fetch_and_upsert(target_date, args.market)
    if n:
        print(f"Done — {n} rows upserted")
    else:
        print("No rows upserted — check stderr for errors")


if __name__ == "__main__":
    main()
