"""
ingest/fetch_proprietary_flows.py — per-ticker proprietary (tự doanh) trading → proprietary_flows.

Source: vnmarket. Its public chain is CafeF -> VNDirect, but CafeF only serves the last ~year, so history before
that has to come from VNDirect directly (internal plan, same values). Values are stored in tỷ đồng like foreign_flows.

Usage (thin wrapper around the same functions the Dagster asset calls):
    python ingest/fetch_proprietary_flows.py --tickers HPG,VCB
    python ingest/fetch_proprietary_flows.py --all-securities          # incremental since latest stored date
    python ingest/fetch_proprietary_flows.py --all-securities --backfill
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

HISTORY_START = date(2022, 5, 17)       # HOSE resumed publishing proprietary data
RECENT_DAYS = 300                       # CafeF covers about a year; stay well inside it for the public chain
OVERLAP_DAYS = 5                        # re-fetch the tail so late-published sessions are picked up
MIN_TICKER_COVERAGE = 0.5               # a run where fewer than half the tickers returned rows is a failure


def to_rows(df: pd.DataFrame) -> list[tuple]:
    """vnmarket frame (VND) -> DB tuples (tỷ đồng)."""
    rows = []
    for r in df.itertuples(index=False):
        rows.append((r.symbol, pd.Timestamp(r.date).date(), round(float(r.buy_value) / 1e9, 4),
                     round(float(r.sell_value) / 1e9, 4), round(float(r.net_value) / 1e9, 4),
                     int(r.buy_volume), int(r.sell_volume), int(r.net_volume)))
    return rows


def _active_tickers() -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM securities WHERE is_active ORDER BY ticker")
        return [r[0] for r in cur.fetchall()]


def _latest_dates() -> dict[str, date]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, MAX(date) FROM proprietary_flows GROUP BY ticker")
        return dict(cur.fetchall())


def _upsert(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO proprietary_flows (ticker, date, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date) DO UPDATE
                SET buy_value=EXCLUDED.buy_value, sell_value=EXCLUDED.sell_value, net_value=EXCLUDED.net_value,
                    buy_volume=EXCLUDED.buy_volume, sell_volume=EXCLUDED.sell_volume, net_volume=EXCLUDED.net_volume,
                    fetched_at=NOW()
            """, rows)
    return len(rows)


def fetch_ticker(vn, ticker: str, start: date) -> pd.DataFrame:
    """Rows from `start` to today. Old starts go to VNDirect directly because CafeF would silently truncate."""
    if (date.today() - start).days > RECENT_DAYS:
        from vnmarket import plans
        from vnmarket.sources import vnd
        return vn.market._x(plans._paged_history([vnd.proprietary], ticker, str(start), None, 5000))
    return vn.market.proprietary(ticker, start=str(start), limit=500)


def run(tickers: list[str] | None = None, backfill: bool = False) -> dict:
    """Upsert proprietary flows. Raises when the run produced nothing (so Dagster retries / alerts)."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData

    tickers = tickers or _active_tickers()
    latest = {} if backfill else _latest_dates()
    rows_total, ok, no_data, failed = 0, 0, [], []
    with VnMarket() as vn:
        for t in tickers:
            start = HISTORY_START if t not in latest else max(HISTORY_START, latest[t] - timedelta(days=OVERLAP_DAYS))
            try:
                df = fetch_ticker(vn, t, start)
            except NoData:
                no_data.append(t)               # normal for names without proprietary activity
                continue
            except Exception as exc:
                failed.append((t, repr(exc)[:120]))
                continue
            rows_total += _upsert(to_rows(df))
            ok += 1
    if tickers and ok < MIN_TICKER_COVERAGE * len(tickers):
        raise RuntimeError(f"proprietary_flows: only {ok}/{len(tickers)} tickers returned data; failed={failed[:10]}")
    return {"rows": rows_total, "tickers_ok": ok, "no_data": len(no_data), "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch proprietary flows -> Postgres")
    ap.add_argument("--all-securities", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="ignore stored dates, refetch from 2022-05-17")
    ap.add_argument("--tickers", default="")
    a = ap.parse_args()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    if not (tickers or a.all_securities):
        ap.error("pass --tickers or --all-securities")
    print(run(tickers, a.backfill))


if __name__ == "__main__":
    main()
