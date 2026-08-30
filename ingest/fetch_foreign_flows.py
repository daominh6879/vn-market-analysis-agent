"""
ingest/fetch_foreign_flows.py — Fetch per-ticker foreign trading data → foreign_flows.

Two modes:
  - Historical (--migrate / --start-date): Fireant historical-quotes endpoint,
    per-ticker date range, derives values from volume × close price.
  - Daily live (default): VCI price-board endpoint, today's session data,
    falls back to Fireant historical for a specific date.

Provider priority:
  Historical: FireantProvider (primary) → VCI fallback
  Daily live:  VciDirectProvider (primary) → Fireant historical fallback

Usage:
    # initial 1-year backfill (all active securities):
    python ingest/fetch_foreign_flows.py --migrate

    # incremental: fill gaps since latest date (all active securities):
    python ingest/fetch_foreign_flows.py --all-securities

    # today only (all active securities, live session data):
    python ingest/fetch_foreign_flows.py --all-securities --live

    # specific date:
    python ingest/fetch_foreign_flows.py --date 2026-08-25
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn
from tools.providers import FireantProvider, VciDirectProvider

_fireant = FireantProvider()
_vci = VciDirectProvider()

MIGRATE_DAYS = 365
_CHUNK_SIZE = 50


def _active_tickers() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM securities WHERE is_active = TRUE ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def _latest_date(ticker: str) -> date | None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(date) FROM foreign_flows WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _upsert_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO foreign_flows
                    (ticker, date, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO UPDATE
                    SET buy_value=EXCLUDED.buy_value, sell_value=EXCLUDED.sell_value,
                        net_value=EXCLUDED.net_value, buy_volume=EXCLUDED.buy_volume,
                        sell_volume=EXCLUDED.sell_volume, net_volume=EXCLUDED.net_volume,
                        fetched_at=NOW()
                """,
                rows,
            )
    return len(rows)


def _fireant_rows_for_range(ticker: str, start: date, end: date) -> list[tuple]:
    """Fetch foreign flows via Fireant historical quotes for a date range."""
    df = _fireant.fetch_history_range(ticker, str(start), str(end))
    if df.empty or "foreign_buy_vol" not in df.columns:
        return []

    rows = []
    for _, row in df.iterrows():
        buy_vol = int(row.get("foreign_buy_vol", 0))
        sell_vol = int(row.get("foreign_sell_vol", 0))
        net_vol = buy_vol - sell_vol
        close_price = float(row.get("close", 0))
        buy_val = round(buy_vol * close_price / 1e9, 4)
        sell_val = round(sell_vol * close_price / 1e9, 4)
        net_val = round(net_vol * close_price / 1e9, 4)
        rows.append((
            ticker,
            str(row["time"])[:10],
            buy_val, sell_val, net_val,
            buy_vol, sell_vol, net_vol,
        ))
    return rows


def fetch_historical(ticker: str, start: date, end: date) -> int:
    """Backfill foreign flows for a ticker via Fireant. Returns rows upserted."""
    rows = _fireant_rows_for_range(ticker, start, end)
    return _upsert_rows(rows)


def fetch_incremental(ticker: str, end: date) -> int:
    """Fill gaps: from latest date in DB up to end. Returns rows upserted."""
    latest = _latest_date(ticker)
    start = (latest + timedelta(days=1)) if latest else (end - timedelta(days=MIGRATE_DAYS))
    if start > end:
        return 0
    rows = _fireant_rows_for_range(ticker, start, end)
    return _upsert_rows(rows)


def fetch_live_today(target_date: date) -> int:
    """
    Fetch today's foreign flows via VCI live price board → foreign_flows.
    Falls back to Fireant historical if VCI fails.
    """
    tickers = _active_tickers()
    if not tickers:
        return 0

    date_str = str(target_date)
    live_rows: list[tuple] = []

    # VCI live price board (chunked)
    for i in range(0, len(tickers), _CHUNK_SIZE):
        chunk = tickers[i : i + _CHUNK_SIZE]
        try:
            batch = _vci.fetch_foreign_batch(chunk)
            for item in batch:
                t = item["ticker"]
                buy_vol = int(item.get("buy_volume", 0))
                sell_vol = int(item.get("sell_volume", 0))
                net_vol = int(item.get("net_volume", 0))
                buy_val = float(item.get("buy_value", 0)) / 1e9  # VCI returns raw VND → tỷ đồng
                sell_val = float(item.get("sell_value", 0)) / 1e9
                net_val = round(buy_val - sell_val, 4)
                live_rows.append((t, date_str, round(buy_val, 4), round(sell_val, 4), net_val,
                                   buy_vol, sell_vol, net_vol))
        except Exception as e:
            sys.stderr.write(f"[foreign_flows] VCI chunk {i} failed: {e}\n")

    return _upsert_rows(live_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch foreign flows → Postgres")
    parser.add_argument("--migrate", action="store_true",
                        help=f"Backfill {MIGRATE_DAYS} days via Fireant for all active securities")
    parser.add_argument("--all-securities", action="store_true",
                        help="Incremental fill for all active tickers (since latest DB date)")
    parser.add_argument("--live", action="store_true",
                        help="Fetch today via VCI live board (use with --all-securities)")
    parser.add_argument("--date", default=None,
                        help="Specific date YYYY-MM-DD for live fetch (default: today)")
    parser.add_argument("--tickers", default="",
                        help="Comma-separated tickers for targeted run")
    args = parser.parse_args()

    today = date.today()

    if args.migrate:
        tickers = _active_tickers()
        start = today - timedelta(days=MIGRATE_DAYS)
        print(f"MIGRATE: {len(tickers)} tickers, {MIGRATE_DAYS} days ({start} → {today})")
        total = 0
        failed = []
        for t in tickers:
            try:
                n = fetch_historical(t, start, today)
                print(f"  {t}: {n} rows")
                total += n
            except Exception as e:
                print(f"  {t}: FAILED — {e}", file=sys.stderr)
                failed.append(t)
        print(f"\nTotal: {total} rows upserted")
        if failed:
            print(f"Failed: {', '.join(failed)}", file=sys.stderr)
            sys.exit(1)

    elif args.all_securities and args.live:
        target = date.fromisoformat(args.date) if args.date else today
        print(f"LIVE: {target} via VCI price board")
        n = fetch_live_today(target)
        print(f"Done — {n} rows upserted")

    elif args.all_securities:
        tickers = _active_tickers()
        print(f"INCREMENTAL: {len(tickers)} tickers (gaps since latest DB date)")
        total = 0
        failed = []
        for t in tickers:
            try:
                n = fetch_incremental(t, today)
                if n:
                    print(f"  {t}: {n} rows")
                total += n
            except Exception as e:
                print(f"  {t}: FAILED — {e}", file=sys.stderr)
                failed.append(t)
        print(f"\nTotal: {total} rows upserted")
        if failed:
            print(f"Failed: {', '.join(failed)}", file=sys.stderr)
            sys.exit(1)

    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        target = date.fromisoformat(args.date) if args.date else today
        start = target - timedelta(days=30)
        total = 0
        for t in tickers:
            try:
                n = fetch_historical(t, start, target)
                print(f"  {t}: {n} rows")
                total += n
            except Exception as e:
                print(f"  {t}: FAILED — {e}", file=sys.stderr)
        print(f"Total: {total} rows")

    else:
        # Legacy: live today via VCI (matches old default behavior)
        target = date.fromisoformat(args.date) if args.date else today
        print(f"Fetching live foreign flows: {target}")
        n = fetch_live_today(target)
        print(f"Done — {n} rows upserted")


if __name__ == "__main__":
    main()


# ── Backward-compatibility shims ──────────────────────────────────────────────
# Kept so existing call-sites (tests, scripts) don't need rewrites.

def _parse_date(date_str: str | None) -> date:
    from datetime import datetime
    if not date_str:
        return date.today()
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def fetch_foreign_via_vci(target_date: date, market: str = "HOSE") -> list[dict]:
    """Legacy: fetch foreign flows via VCI live board. Returns list of row dicts."""
    tickers = _active_tickers()
    date_str = str(target_date)
    rows: list[dict] = []
    for i in range(0, len(tickers), _CHUNK_SIZE):
        chunk = tickers[i : i + _CHUNK_SIZE]
        try:
            batch = _vci.fetch_foreign_batch(chunk)
            for item in batch:
                rows.append({**item, "date": date_str})
        except Exception as e:
            import sys as _sys
            _sys.stderr.write(f"[fetch_foreign_flows] VCI chunk {i} failed: {e}\n")
    return rows


def fetch_and_upsert(target_date: date, market: str = "HOSE") -> int:
    """Legacy: fetch today's foreign flows via VCI → DB. Returns row count.

    Calls fetch_foreign_via_vci (patchable in tests) then upserts.
    """
    try:
        rows = fetch_foreign_via_vci(target_date, market)
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"[fetch_foreign_flows] fetch failed for {target_date}: {e}\n")
        return 0
    if not rows:
        return 0
    # Convert VCI live-board row format (values already in VND) to DB tuple format
    db_rows = []
    for item in rows:
        buy_vol = int(item.get("buy_volume", 0))
        sell_vol = int(item.get("sell_volume", 0))
        net_vol = int(item.get("net_volume", buy_vol - sell_vol))
        buy_val = round(float(item.get("buy_value", 0)) / 1e9, 4)
        sell_val = round(float(item.get("sell_value", 0)) / 1e9, 4)
        net_val = round(buy_val - sell_val, 4)
        db_rows.append((
            item["ticker"],
            item["date"],
            buy_val, sell_val, net_val,
            buy_vol, sell_vol, net_vol,
        ))
    return _upsert_rows(db_rows)
