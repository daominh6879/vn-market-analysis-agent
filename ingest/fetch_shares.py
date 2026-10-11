"""
ingest/fetch_shares.py — Fill securities.listed_shares (số cổ phiếu lưu hành) for market cap.

market_cap = listed_shares × latest close, used by market_data (top vốn hóa) and screening.

Sources, in order:
  1. vnstock VCI Company.overview() → `issue_share`
  2. Fallback for tickers still at 0: charter capital in financial_facts (`von_dieu_le`)
     ÷ 10,000 VND par value (all VN listed shares are par 10,000) — flagged as approximate.

Usage:
    python ingest/fetch_shares.py                    # all active tickers with listed_shares = 0
    python ingest/fetch_shares.py --all              # refresh every active ticker
    python ingest/fetch_shares.py --tickers VNM,HPG
    python ingest/fetch_shares.py --charter-only     # skip vnstock, DB fallback only
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

PAR_VALUE_VND = 10_000


CALL_TIMEOUT_S = 30   # vnstock has no request timeout of its own; a stuck call must not hang the job


def _shares_from_overview(ticker: str, source: str, column: str) -> int | None:
    from vnstock import Company
    df = Company(symbol=ticker, source=source).overview()
    if df is None or df.empty or column not in df.columns:
        return None
    try:
        v = float(df[column].iloc[0])
    except (TypeError, ValueError):
        return None
    return int(v) if not math.isnan(v) and v > 0 else None


MIN_CALL_INTERVAL_S = 1.2   # vnstock community tier: 60 requests/minute
RATE_LIMIT_WAIT_S = 65
_last_call = 0.0


def _with_timeout(fn, *args):
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn, *args).result(timeout=CALL_TIMEOUT_S)
    finally:
        ex.shutdown(wait=False)   # abandon a stuck worker instead of blocking on it


def _paced_call(ticker: str, source: str, column: str) -> int | None:
    """One vnstock request, spaced under the rate limit. vnstock raises SystemExit when the limit is hit:
    wait out the window and retry once instead of letting it kill the process."""
    global _last_call
    for attempt in (1, 2):
        time.sleep(max(0.0, MIN_CALL_INTERVAL_S - (time.monotonic() - _last_call)))
        _last_call = time.monotonic()
        try:
            return _with_timeout(_shares_from_overview, ticker, source, column)
        except SystemExit:
            if attempt == 2:
                return None
            time.sleep(RATE_LIMIT_WAIT_S)
        except Exception:
            return None
    return None


def fetch_issue_share(ticker: str) -> int | None:
    """Shares outstanding: KBS `outstanding_shares` (fast), then VCI `issue_share`. None when unavailable."""
    for source, column in (("KBS", "outstanding_shares"), ("VCI", "issue_share")):
        n = _paced_call(ticker, source, column)
        if n:
            return n
    return None


def update_shares(ticker: str, shares: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE securities SET listed_shares = %s, updated_at = NOW() WHERE ticker = %s",
                        (shares, ticker))


def fill_from_charter_capital() -> int:
    """listed_shares = latest charter capital / 10,000 for tickers still at 0. Returns rows updated."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE securities s
                SET listed_shares = ROUND(f.value / %s), updated_at = NOW()
                FROM (
                    SELECT DISTINCT ON (ticker) ticker, value
                    FROM financial_facts
                    WHERE metric_code = 'von_dieu_le' AND period ~ '^[0-9]{4}$' AND value > 0
                    ORDER BY ticker, period DESC
                ) f
                WHERE s.ticker = f.ticker AND COALESCE(s.listed_shares, 0) = 0
                """,
                (PAR_VALUE_VND,),
            )
            return cur.rowcount


def _target_tickers(args) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    where = "is_active" if args.all else "is_active AND COALESCE(listed_shares, 0) = 0"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT ticker FROM securities WHERE {where} ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill securities.listed_shares")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--all", action="store_true", help="Refresh every active ticker, not just missing")
    parser.add_argument("--charter-only", action="store_true", help="Skip vnstock; use charter capital only")
    args = parser.parse_args()

    ok, failed = 0, []
    if not args.charter_only:
        tickers = _target_tickers(args)
        print(f"listed_shares: {len(tickers)} tickers via vnstock VCI")
        for t in tickers:
            try:
                n = fetch_issue_share(t)
                if n:
                    update_shares(t, n)
                    ok += 1
                    print(f"  {t}: {n:,}")
                else:
                    failed.append(t)
            except Exception as exc:
                print(f"  {t}: FAILED — {exc}", file=sys.stderr)
                failed.append(t)
            time.sleep(3.2)  # stay under vnstock rate limit

    approx = fill_from_charter_capital()
    print(f"\nvnstock: {ok} updated, {len(failed)} missing. "
          f"Charter-capital fallback (approx, ÷{PAR_VALUE_VND:,}): {approx} updated.")
    if failed:
        print(f"Missing from vnstock: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
