"""
scripts/populate_financial_data.py — One-shot populate financial_facts + stock_prices.

Reads TICKERS from .env, fetches via vnstock, inserts into Postgres.

Usage:
  python scripts/populate_financial_data.py
  python scripts/populate_financial_data.py --tickers HPG,VCB,FPT --from 2020 --to 2025
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=os.getenv("TICKERS", "HPG"))
    parser.add_argument("--from", dest="period_from", type=int, default=2020)
    parser.add_argument("--to",   dest="period_to",   type=int, default=2025)
    parser.add_argument("--prices-from", default="2022-01-01")
    parser.add_argument("--prices-to",   default="2026-12-31")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-prices",     action="store_true")
    args = parser.parse_args()

    ticker_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    print(f"Tickers: {ticker_list}")

    # --- Financial facts ---
    if not args.skip_financials:
        from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts
        total_facts = 0
        for ticker in ticker_list:
            print(f"\n[{ticker}] Fetching financials ({args.period_from}–{args.period_to})...")
            try:
                facts = fetch_finance_facts(
                    ticker=ticker,
                    report_type="consolidated",
                    period_from=args.period_from,
                    period_to=args.period_to,
                    source="VCI",
                )
                if facts:
                    n = insert_vnstock_facts(facts)
                    total_facts += n
                    print(f"  → {n} facts inserted")
                else:
                    print(f"  → 0 facts returned")
            except Exception as e:
                print(f"  ERROR: {e}")
        print(f"\nFinancials done: {total_facts} total facts")

    # --- Stock prices ---
    if not args.skip_prices:
        from ingest.fetch_prices import fetch_and_insert
        total_rows = 0
        for ticker in ticker_list:
            print(f"\n[{ticker}] Fetching prices ({args.prices_from} → {args.prices_to})...")
            try:
                n = fetch_and_insert(ticker, args.prices_from, args.prices_to)
                total_rows += n
                print(f"  → {n} rows inserted")
            except Exception as e:
                print(f"  ERROR: {e}")
        print(f"\nPrices done: {total_rows} total rows")

    # --- Verify ---
    from data.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM financial_facts")
            ff_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM stock_prices")
            sp_count = cur.fetchone()[0]
    print(f"\n=== DB state after populate ===")
    print(f"  financial_facts: {ff_count} rows")
    print(f"  stock_prices:    {sp_count} rows")


if __name__ == "__main__":
    main()
