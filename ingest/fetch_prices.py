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

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.db import get_conn


def fetch_and_insert(ticker: str, from_date: str, to_date: str) -> int:
    try:
        from vnstock.api.quote import Quote  # type: ignore[import]
    except ImportError:
        print("vnstock chưa cài. Chạy: pip install vnstock")
        sys.exit(1)

    q = Quote(symbol=ticker, source="VCI")
    df = q.history(start=from_date, end=to_date, interval="1D")

    if df is None or df.empty:
        print(f"Không có dữ liệu giá cho {ticker} ({from_date} → {to_date})")
        return 0

    # vnstock trả về cột: time, open, high, low, close, volume
    # close là giá đã điều chỉnh theo mặc định của VCI source
    df = df.rename(columns={"time": "ngay", "close": "close_adj"})
    rows = [
        (ticker, str(row["ngay"])[:10], float(row["close_adj"]), int(row.get("volume", 0)))
        for _, row in df.iterrows()
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stock_prices (ticker, ngay, close_adj, volume)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker, ngay) DO UPDATE
                    SET close_adj = EXCLUDED.close_adj,
                        volume    = EXCLUDED.volume
                """,
                rows,
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tải giá cổ phiếu vào Postgres")
    parser.add_argument("--ticker", default="HPG")
    parser.add_argument("--from",   dest="from_date", default="2022-01-01")
    parser.add_argument("--to",     dest="to_date",   default="2024-12-31")
    args = parser.parse_args()

    print(f"Tải giá {args.ticker} ({args.from_date} → {args.to_date})...")
    n = fetch_and_insert(args.ticker, args.from_date, args.to_date)
    print(f"Đã insert {n} rows vào stock_prices")


if __name__ == "__main__":
    main()
