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
from datetime import date, datetime, timedelta
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


# HOSE daily price limit is ±7%. A close-to-close move beyond this cannot be a normal
# trading day: it is an ex-rights / stock-dividend / split day. Fireant serves RAW prices
# (history before the ex-date is not adjusted) while KBS/VCI serve ADJUSTED prices, so after
# such a day the stored series has a fake cliff (HDB 28.0 → 22.4 on its 25% stock dividend).
_PRICE_LIMIT_BREAK = 0.075


def _has_unadjusted_break(ticker: str, since: date) -> bool:
    """True if ohlcv_daily has a close-to-close move beyond the daily price limit since `since`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM (
                    SELECT date, close, LAG(close) OVER (ORDER BY date) AS prev_close
                    FROM ohlcv_daily WHERE ticker = %s AND date >= %s
                ) t
                WHERE prev_close > 0 AND ABS(close / prev_close - 1) > %s
                LIMIT 1
                """,
                (ticker, since - timedelta(days=10), _PRICE_LIMIT_BREAK),
            )
            return cur.fetchone() is not None


def _adjusted_closes(ticker: str, days: int) -> dict[str, dict]:
    """date → adjusted {open,high,low,close} in nghìn đồng. VCI first (complete), KBS fills gaps
    (KBS history skips sessions). Both return VND from get_history."""
    out: dict[str, dict] = {}
    for prov in (VciDirectProvider(), KbsProvider()):
        try:
            df = prov.get_history(resolve_ticker(ticker), days)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            d = str(r["time"])[:10]
            if d not in out:
                out[d] = {k: float(r[k]) / 1000.0 for k in ("open", "high", "low", "close")}
    return out


def refresh_adjusted_prices(ticker: str, days: int = MIGRATE_DAYS) -> int:
    """Re-align stored OHLC to the adjusted series. Volume is left untouched.

    Sessions the adjusted sources do not cover are scaled by the adjusted/raw factor of the
    nearest covered session, so the series stays continuous. Returns rows updated.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT date, open, high, low, close FROM ohlcv_daily "
                        "WHERE ticker = %s ORDER BY date", (ticker,))
            stored = [(str(d)[:10], *map(float, vals)) for d, *vals in cur.fetchall()]
    if not stored:
        return 0
    # Cover the WHOLE stored history, otherwise rows older than the window keep raw prices and
    # a cliff appears at the window edge.
    days = max(days, (date.today() - date.fromisoformat(stored[0][0])).days + 30)
    adj = _adjusted_closes(ticker, days)
    if not adj:
        return 0
    factors = {d: adj[d]["close"] / c for d, _, _, _, c in stored if d in adj and c > 0}
    if not factors:
        return 0
    covered = sorted(factors)
    rows = []
    for d, o, h, l, c in stored:
        if d in adj:
            a = adj[d]
            rows.append((a["open"], a["high"], a["low"], a["close"], ticker, d))
            continue
        prev = [x for x in covered if x < d]
        f = factors[prev[-1]] if prev else factors[covered[0]]
        rows.append((o * f, h * f, l * f, c * f, ticker, d))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE ohlcv_daily SET open=%s, high=%s, low=%s, close=%s, fetched_at=NOW() "
                "WHERE ticker=%s AND date=%s",
                rows,
            )
    return len(rows)


def _upsert_foreign(rows: list[tuple]) -> int:
    """Insert foreign_flows rows, never overwriting existing ones.

    Row = (ticker, date, buy_val, sell_val, net_val, buy_vol, sell_vol, net_vol).

    DO NOTHING (not DO UPDATE) on purpose: values here are *derived*
    (foreign volume x close price) because Fireant only exposes foreign
    volume. VCI's price board (ingest/fetch_foreign_flows.py) reports the
    real traded value, so this path only fills gaps and must not clobber
    a row VCI already wrote.
    """
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO foreign_flows
                    (ticker, date, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO NOTHING
                """,
                rows,
            )
            # DO NOTHING means existing rows are skipped, so report what was
            # actually inserted rather than the number of candidate rows.
            inserted = cur.rowcount
    return inserted if inserted is not None and inserted >= 0 else len(rows)


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
                ].copy()
            # get_history returns VND; ohlcv_daily (and the Fireant path) store nghìn đồng
            for col in ("open", "high", "low", "close"):
                if col in df_fallback.columns:
                    df_fallback[col] = df_fallback[col] / 1000.0
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

    # Fireant is raw: if this window contains an ex-rights cliff, re-align the whole stored
    # history to the adjusted series so technicals/returns don't see a fake crash.
    if used_fireant and _has_unadjusted_break(ticker, range_start):
        refresh_adjusted_prices(ticker)

    # Foreign rows (Fireant only)
    n_foreign = 0
    if used_fireant and "foreign_buy_vol" in df.columns:
        foreign_rows = []
        for _, row in df.iterrows():
            buy_vol = int(row.get("foreign_buy_vol", 0))
            sell_vol = int(row.get("foreign_sell_vol", 0))
            net_vol = buy_vol - sell_vol
            close_price = float(row.get("close", 0))
            # derive value in tỷ đồng. Fireant `close` is in nghìn đồng
            # (HPG 21.7 = 21,700 VND), so shares × close is already in
            # nghìn đồng → /1e6 gives tỷ đồng.
            buy_val = round(buy_vol * close_price / 1e6, 4)
            sell_val = round(sell_vol * close_price / 1e6, 4)
            net_val = round(net_vol * close_price / 1e6, 4)
            foreign_rows.append((
                ticker,
                str(row["time"])[:10],
                buy_val, sell_val, net_val,
                buy_vol, sell_vol, net_vol,
            ))
        n_foreign = _upsert_foreign(foreign_rows)

    return {"ohlcv": n_ohlcv, "foreign": n_foreign}


def _explicit_range(args) -> tuple[date | None, date | None]:
    """Parse --start-date/--end-date, or (None, None) for incremental mode.

    Incremental mode starts at `MAX(date) + 1`, so it can never revisit a hole
    in the middle of the series. An explicit range is the only way to refill one.
    """
    if not args.start_date:
        return None, None
    return (
        datetime.strptime(args.start_date, "%Y-%m-%d").date(),
        datetime.strptime(args.end_date, "%Y-%m-%d").date(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OHLCV (+ foreign via Fireant) → Postgres")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers")
    parser.add_argument("--all-securities", action="store_true",
                        help="Use all active tickers from securities table")
    parser.add_argument("--migrate", action="store_true",
                        help=f"Backfill {MIGRATE_DAYS} days for all active securities")
    parser.add_argument("--days", type=int, default=30,
                        help="Days to fetch (non-migrate incremental, default 30)")
    parser.add_argument("--repair-adjustments", action="store_true",
                        help="Re-align stored OHLC to adjusted prices for tickers with an ex-rights cliff")
    parser.add_argument("--start-date", default=None,
                        help="Explicit range start YYYY-MM-DD (use with --end-date to fill a gap)")
    parser.add_argument("--end-date", default=None,
                        help="Explicit range end YYYY-MM-DD")
    args = parser.parse_args()

    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be given together")
        return

    explicit_range = bool(args.start_date)

    if args.repair_adjustments:
        fixed = 0
        for t in (_active_tickers() if not args.tickers else
                  [x.strip().upper() for x in args.tickers.split(",") if x.strip()]):
            if _has_unadjusted_break(t, date(2000, 1, 1)):
                n = refresh_adjusted_prices(t)
                print(f"  {t}: re-aligned {n} rows to adjusted prices")
                fixed += 1
        print(f"Repaired {fixed} tickers")
        return

    if args.migrate:
        tickers = _active_tickers()
        start = date.today() - timedelta(days=MIGRATE_DAYS)
        end = date.today()
        print(f"MIGRATE: {len(tickers)} tickers, {MIGRATE_DAYS} days ({start} → {end})")
    elif args.all_securities:
        tickers = _active_tickers()
        start, end = _explicit_range(args)
        if explicit_range:
            print(f"RANGE: {len(tickers)} active tickers ({start} → {end})")
        else:
            print(f"INCREMENTAL: {len(tickers)} active tickers")
    elif args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        start, end = _explicit_range(args)
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
            if args.migrate or explicit_range:
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
