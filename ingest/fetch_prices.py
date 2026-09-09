"""
ingest/fetch_prices.py — Bài 12: Tải giá cổ phiếu đã điều chỉnh vào Postgres.

Dùng giá close_adj — quan trọng cho bài 19 (chỉ báo kỹ thuật).
Giá chưa điều chỉnh làm chỉ báo sai tại ngày chia cổ tức.

Cài đặt: pip install vnstock
Chạy: python ingest/fetch_prices.py --ticker HPG --from 2022-01-01 --to 2024-12-31
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.db import get_conn


def _latest_price_date(ticker: str) -> str | None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) FROM stock_prices WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def _last_weekday(d: date) -> date:
    """Most recent Mon–Fri on/before `d` (market-closed weekend guard)."""
    from datetime import date, timedelta
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fetch_and_insert(ticker: str, from_date: str, to_date: str) -> int:
    from datetime import date, timedelta

    latest = _latest_price_date(ticker)
    # "Up to date" = latest covers the last weekday, not calendar today — on
    # weekends the market is closed, so a Saturday run must skip rather than
    # re-fetch every ticker and burn the vnstock rate limit for nothing.
    if latest and latest >= _last_weekday(date.today()).isoformat():
        return 0  # already up to date

    if latest and latest > from_date:
        from_date = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()  # skip already-fetched day

    try:
        from vnstock.api.quote import Quote  # type: ignore[import]
    except ImportError:
        print("vnstock chưa cài. Chạy: pip install vnstock")
        sys.exit(1)

    df = None
    for source in ("kbs", "VCI"):
        try:
            q = Quote(symbol=ticker, source=source)
            df = q.history(start=from_date, end=to_date, interval="1D")
            if df is not None and not df.empty:
                break
        except SystemExit:
            continue  # rate limited → try next source
        except Exception:
            continue

    if df is None or df.empty:
        print(f"Không có dữ liệu giá cho {ticker} ({from_date} → {to_date})")
        return 0

    # vnstock trả về cột: time, open, high, low, close, volume
    # close là giá đã điều chỉnh theo mặc định của VCI source
    df = df.rename(columns={"time": "ngay", "close": "close_adj"})
    rows = []
    for _, row in df.iterrows():
        ngay = str(row["ngay"])[:10]
        close_adj = float(row["close_adj"]) if pd.notna(row.get("close_adj")) else None
        volume = int(row["volume"]) if pd.notna(row.get("volume")) else 0
        if close_adj is None:
            continue  # skip rows with no price
        rows.append((ticker, ngay, close_adj, volume))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stock_prices (ticker, trade_date, close_adj, volume)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker, trade_date) DO UPDATE
                    SET close_adj = EXCLUDED.close_adj,
                        volume    = EXCLUDED.volume
                """,
                rows,
            )
    return len(rows)


def main() -> None:
    import time
    from datetime import date, timedelta

    parser = argparse.ArgumentParser(description="Tải giá cổ phiếu vào Postgres")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--all-securities", action="store_true",
                        help="Dùng tất cả active tickers từ securities table")
    parser.add_argument("--days-back", type=int, default=2,
                        help="Số ngày lùi cho --all-securities (default 2)")
    parser.add_argument("--from",   dest="from_date", default="2022-01-01")
    parser.add_argument("--to",     dest="to_date",   default="2024-12-31")
    args = parser.parse_args()

    today = date.today().isoformat()

    if args.all_securities:
        from core.tickers import get_tickers
        tickers = get_tickers()
        from_date = str(date.today() - timedelta(days=args.days_back))
        print(f"INCREMENTAL: {len(tickers)} active tickers ({from_date} → {today})")
        total = 0
        failed = []
        for t in tickers:
            try:
                n = fetch_and_insert(t, from_date, today)
                total += n
                if n:
                    print(f"  {t}: {n} rows")
            except Exception as e:
                print(f"  {t}: FAILED — {e}", file=sys.stderr)
                failed.append(t)
            time.sleep(3.2)  # stay under vnstock 20 req/min rate limit
        print(f"\nTotal: {total} rows upserted")
        if failed:
            print(f"Failed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)
            sys.exit(1)
        return

    ticker = args.ticker or "HPG"
    print(f"Tải giá {ticker} ({args.from_date} → {args.to_date})...")
    n = fetch_and_insert(ticker, args.from_date, args.to_date)
    print(f"Đã insert {n} rows vào stock_prices")


if __name__ == "__main__":
    main()
