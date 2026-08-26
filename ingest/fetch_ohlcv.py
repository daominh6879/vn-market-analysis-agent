"""
ingest/fetch_ohlcv.py — Fetch OHLCV via VciDirectProvider → upsert ohlcv_daily.

Uses VciDirectProvider directly (not vnstock) for OHLCV + full O/H/L/C columns.
Designed for batch: call fetch_and_upsert for each ticker.

Usage:
    python ingest/fetch_ohlcv.py --tickers VN30,HPG,FPT --days 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.db import get_conn
from tools.providers import VciDirectProvider, resolve_ticker


_provider = VciDirectProvider()


def fetch_and_upsert(ticker: str, days: int = 30) -> int:
    """Fetch `days` phiên OHLCV từ VCI và upsert vào ohlcv_daily. Trả số rows upserted."""
    resolved = resolve_ticker(ticker)
    try:
        df = _provider.get_history(resolved, days)
    except Exception as e:
        raise RuntimeError(f"VCI fetch failed for '{ticker}' (resolved='{resolved}'): {e}") from e

    if df.empty:
        return 0

    rows = [
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

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ohlcv_daily (ticker, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO UPDATE
                    SET open       = EXCLUDED.open,
                        high       = EXCLUDED.high,
                        low        = EXCLUDED.low,
                        close      = EXCLUDED.close,
                        volume     = EXCLUDED.volume,
                        fetched_at = NOW()
                """,
                rows,
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers",  default="", help="Comma-separated tickers")
    parser.add_argument("--universe", choices=["hose", "vn30"], default="",
                        help="hose = full HOSE seed (~150 mã); vn30 = VN30 only")
    parser.add_argument("--days",     type=int, default=30)
    args = parser.parse_args()

    if args.universe == "hose":
        from data.hose_universe import load_hose_tickers
        ticker_list = load_hose_tickers()
    elif args.universe == "vn30":
        from data.hose_universe import get_vn30_tickers
        ticker_list = get_vn30_tickers()
    else:
        ticker_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    if not ticker_list:
        parser.error("Provide --tickers or --universe (hose|vn30)")

    total = 0
    for t in ticker_list:
        try:
            n = fetch_and_upsert(t, args.days)
            print(f"  {t}: {n} rows")
            total += n
        except Exception as e:
            print(f"  {t}: FAILED — {e}", file=sys.stderr)
    print(f"Total: {total} rows upserted")


if __name__ == "__main__":
    main()
