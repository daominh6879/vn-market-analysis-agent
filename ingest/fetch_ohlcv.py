"""
ingest/fetch_ohlcv.py — Fetch OHLCV → ohlcv_daily (+ foreign_flows when Fireant).

Provider priority: Fireant → VCI → TCBS
Fireant response includes buyForeignQuantity/sellForeignQuantity, so we upsert
foreign_flows in the same pass when the Fireant source is used.

Usage:
    # initial 1-year backfill (all active securities):
    python ingest/fetch_ohlcv.py --migrate

    # incremental daily (all active securities, only missing days):
    python ingest/fetch_ohlcv.py --all-securities

    # specific tickers, specific days:
    python ingest/fetch_ohlcv.py --tickers HPG,VCB,FPT --days 30
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
from tools.providers import (
    FallbackProvider,
    FireantProvider,
    KbsProvider,
    VciDirectProvider,
    resolve_ticker,
)

# Fireant primary; VCI/KBS fallback (no foreign data on fallback path)
_fireant = FireantProvider()
_fallback = FallbackProvider(KbsProvider(), VciDirectProvider())

MIGRATE_DAYS = 365


def _active_tickers() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM securities WHERE is_active = TRUE ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def _latest_date(ticker: str) -> date | None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(date) FROM ohlcv_daily WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _upsert_ohlcv(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ohlcv_daily (ticker, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO UPDATE
                    SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                        close=EXCLUDED.close, volume=EXCLUDED.volume, fetched_at=NOW()
                """,
                rows,
            )
    return len(rows)


def _upsert_foreign(rows: list[tuple]) -> int:
    """Upsert foreign_flows rows. Row = (ticker, date, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol)."""
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


def fetch_and_upsert(
    ticker: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    days: int = 30,
    backfill: bool = False,
) -> dict:
    """
    Fetch OHLCV (and foreign volumes if Fireant) for ticker.

    When start_date/end_date given: use that range.
    Otherwise incremental: only fetch since latest date in DB.
    backfill=True: ignore DB state, always fetch `days` rows.

    Returns {"ohlcv": N, "foreign": N}.
    """
    today = date.today()
    resolved = resolve_ticker(ticker)

    # Determine date range
    if start_date and end_date:
        range_start = start_date
        range_end = end_date
    else:
        latest = _latest_date(ticker) if not backfill else None
        if latest and latest >= today:
            return {"ohlcv": 0, "foreign": 0}
        if latest and not backfill:
            range_start = latest + timedelta(days=1)
        else:
            range_start = today - timedelta(days=days)
        range_end = today

    if range_start > range_end:
        return {"ohlcv": 0, "foreign": 0}

    # Try Fireant first (gives OHLCV + foreign)
    df = None
    used_fireant = False
    try:
        df = _fireant.fetch_history_range(
            resolved,
            str(range_start),
            str(range_end),
        )
        used_fireant = True
    except Exception as fireant_err:
        # Fallback: VCI/KBS (no foreign data)
        try:
            delta_days = (range_end - range_start).days + 10
            df_fallback = _fallback.get_history(resolved, delta_days)
            if not df_fallback.empty:
                df_fallback = df_fallback[
                    (df_fallback["time"] >= str(range_start))
                    & (df_fallback["time"] <= str(range_end))
                ]
            df = df_fallback
        except Exception as fb_err:
            raise RuntimeError(
                f"OHLCV fetch failed for '{ticker}': "
                f"Fireant: {fireant_err}; Fallback: {fb_err}"
            ) from fb_err

    if df is None or df.empty:
        return {"ohlcv": 0, "foreign": 0}

    # OHLCV rows
    ohlcv_rows = [
        (
            ticker,
            str(row["time"])[:10],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row.get("volume", 0)),
        )
        for _, row in df.iterrows()
    ]
    n_ohlcv = _upsert_ohlcv(ohlcv_rows)

    # Foreign rows (Fireant only)
    n_foreign = 0
    if used_fireant and "foreign_buy_vol" in df.columns:
        foreign_rows = []
        for _, row in df.iterrows():
            buy_vol = int(row.get("foreign_buy_vol", 0))
            sell_vol = int(row.get("foreign_sell_vol", 0))
            net_vol = buy_vol - sell_vol
            close_price = float(row.get("close", 0))
            # derive value in tỷ đồng: shares × price / 1e9
            buy_val = round(buy_vol * close_price / 1e9, 4)
            sell_val = round(sell_vol * close_price / 1e9, 4)
            net_val = round(net_vol * close_price / 1e9, 4)
            foreign_rows.append((
                ticker,
                str(row["time"])[:10],
                buy_val, sell_val, net_val,
                buy_vol, sell_vol, net_vol,
            ))
        n_foreign = _upsert_foreign(foreign_rows)

    return {"ohlcv": n_ohlcv, "foreign": n_foreign}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OHLCV (+ foreign via Fireant) → Postgres")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers")
    parser.add_argument("--all-securities", action="store_true",
                        help="Use all active tickers from securities table")
    parser.add_argument("--migrate", action="store_true",
                        help=f"Backfill {MIGRATE_DAYS} days for all active securities")
    parser.add_argument("--days", type=int, default=30,
                        help="Days to fetch (non-migrate incremental, default 30)")
    args = parser.parse_args()

    if args.migrate:
        tickers = _active_tickers()
        start = date.today() - timedelta(days=MIGRATE_DAYS)
        end = date.today()
        print(f"MIGRATE: {len(tickers)} tickers, {MIGRATE_DAYS} days ({start} → {end})")
    elif args.all_securities:
        tickers = _active_tickers()
        start = end = None
        print(f"INCREMENTAL: {len(tickers)} active tickers")
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        start = end = None
    else:
        parser.error("Provide --tickers, --all-securities, or --migrate")
        return

    if not tickers:
        print("No tickers found.")
        sys.exit(1)

    total_ohlcv = total_foreign = 0
    failed = []

    for t in tickers:
        try:
            if args.migrate:
                result = fetch_and_upsert(t, start_date=start, end_date=end, backfill=True)
            else:
                result = fetch_and_upsert(t, days=args.days)
            print(f"  {t}: ohlcv={result['ohlcv']} foreign={result['foreign']}")
            total_ohlcv += result["ohlcv"]
            total_foreign += result["foreign"]
        except Exception as e:
            print(f"  {t}: FAILED — {e}", file=sys.stderr)
            failed.append(t)

    print(f"\nTotal: ohlcv={total_ohlcv} foreign={total_foreign} rows upserted")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
