"""
scripts/backfill_ohlcv.py — Backfill OHLCV history for all tickers.

Sources (in priority order):
  1. Tickers already in ohlcv_daily (DB)
  2. HOSE universe seed / cache
  3. --extra flag for ad-hoc additions

Usage:
    python scripts/backfill_ohlcv.py                    # all DB tickers + HOSE, 365 days
    python scripts/backfill_ohlcv.py --days 500         # longer history
    python scripts/backfill_ohlcv.py --source db        # only tickers already in DB
    python scripts/backfill_ohlcv.py --source hose      # only HOSE universe
    python scripts/backfill_ohlcv.py --extra VPB,SSB    # add specific tickers on top
    python scripts/backfill_ohlcv.py --dry-run          # print ticker list, no fetch
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _tickers_in_db() -> list[str]:
    from core.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ticker FROM ohlcv_daily ORDER BY ticker")
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[warn] Cannot read DB tickers: {e}", file=sys.stderr)
        return []


def _build_ticker_list(source: str, extra: list[str]) -> list[str]:
    tickers: list[str] = []

    if source in ("db", "all"):
        tickers.extend(_tickers_in_db())

    if source in ("hose", "all"):
        from data.hose_universe import load_hose_tickers
        tickers.extend(load_hose_tickers())

    tickers.extend(extra)

    # Deduplicate, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for t in tickers:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--days", type=int, default=365,
        help="Max days of history to backfill per ticker (default: 365)",
    )
    parser.add_argument(
        "--source", choices=["db", "hose", "all"], default="all",
        help="Ticker source: db=already in ohlcv_daily, hose=HOSE universe, all=both (default)",
    )
    parser.add_argument(
        "--extra", default="",
        help="Comma-separated tickers to add on top of --source",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds between API calls to avoid rate-limit (default: 0.3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print ticker list without fetching",
    )
    args = parser.parse_args()

    extra = [t for t in args.extra.split(",") if t.strip()]
    tickers = _build_ticker_list(args.source, extra)

    if not tickers:
        print("No tickers found. Check --source or provide --extra.", file=sys.stderr)
        sys.exit(1)

    print(f"Backfill plan: {len(tickers)} tickers, {args.days} days each")
    if args.dry_run:
        print("Tickers:", ", ".join(tickers))
        return

    from ingest.fetch_ohlcv import fetch_and_upsert

    total_rows = 0
    failed: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        try:
            n = fetch_and_upsert(ticker, days=args.days, backfill=True)
            status = f"{n} rows" if n > 0 else "up-to-date"
            print(f"[{i:3d}/{len(tickers)}] {ticker}: {status}")
            total_rows += n
        except Exception as e:
            print(f"[{i:3d}/{len(tickers)}] {ticker}: FAILED — {e}", file=sys.stderr)
            failed.append(ticker)

        if args.delay > 0 and i < len(tickers):
            time.sleep(args.delay)

    print(f"\nDone. {total_rows} rows upserted across {len(tickers) - len(failed)} tickers.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
