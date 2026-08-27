"""
ingest/fetch_index.py — Fetch VN market index OHLCV from SSI iBoard and upsert to DB.

Usage:
    python ingest/fetch_index.py                    # all default indices, 30 days
    python ingest/fetch_index.py --indices VNINDEX HNX --days 60
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

_DEFAULT_INDICES = ["VNINDEX", "HNX", "UPCOM", "VN30", "HNX30"]
_DEFAULT_DAYS = 30


def fetch_and_upsert(index_code: str, days: int = _DEFAULT_DAYS) -> int:
    """
    Fetch `days` candles for `index_code` from SSI iBoard, upsert to market_index_daily.
    Returns count of rows upserted (0 on error).
    """
    from tools.providers import SsiIndexProvider
    from tools.index_db import upsert_index_rows

    provider = SsiIndexProvider()
    try:
        df = provider.fetch_history(index_code, days)
    except Exception as e:
        sys.stderr.write(f"[fetch_index] {index_code}: SSI fetch failed: {e}\n")
        return 0

    if df.empty:
        sys.stderr.write(f"[fetch_index] {index_code}: empty response\n")
        return 0

    rows: list[dict] = []
    prev_close: float | None = None
    for _, row in df.iterrows():
        close = float(row["close"])
        change_pts = round(close - prev_close, 2) if prev_close is not None else 0.0
        change_pct = round(change_pts / prev_close * 100, 2) if prev_close else 0.0
        rows.append({
            "index_code":      index_code.upper(),
            "date":            str(row["time"]),
            "open":            float(row["open"]),
            "high":            float(row["high"]),
            "low":             float(row["low"]),
            "close":           close,
            "change_pts":      change_pts,
            "change_pct":      change_pct,
            "matched_volume":  int(row.get("volume", 0)),
            "matched_value":   float(row.get("accumulated_value_vnd", 0)),
            "foreign_net":     0.0,
        })
        prev_close = close

    from tools.index_db import upsert_index_rows
    n = upsert_index_rows(rows)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch VN index OHLCV → Postgres")
    parser.add_argument("--indices", nargs="+", default=_DEFAULT_INDICES,
                        help="Index codes to fetch (default: all 5)")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS,
                        help="Calendar days of history (default: 30)")
    args = parser.parse_args()

    total = 0
    for code in args.indices:
        n = fetch_and_upsert(code, args.days)
        print(f"  {code}: {n} rows upserted")
        total += n
    print(f"Done — {total} total rows")


if __name__ == "__main__":
    main()
