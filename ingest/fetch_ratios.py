"""
ingest/fetch_ratios.py — Fetch KBS valuation ratios → stock_ratios.

Single-command daily refresh of P/E, P/B, ROE, EPS… for VN30+VN100 tickers.
Mirrors the Dagster asset vnstock_ratios (pipeline/assets_vnstock.py).

Usage:
    python ingest/fetch_ratios.py                 # all ratio tickers (get_ratio_tickers)
    python ingest/fetch_ratios.py --tickers HPG,VCB
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

_UPSERT_SQL = """
INSERT INTO stock_ratios (
    ticker, fetched_at,
    pe, pb, roe_pct, roa_pct, eps,
    gross_margin_pct, net_margin_pct,
    revenue_growth_pct, earnings_growth_pct,
    de_ratio, ev_ebitda
) VALUES (
    %(ticker)s, NOW(),
    %(pe)s, %(pb)s, %(roe_pct)s, %(roa_pct)s, %(eps)s,
    %(gross_margin_pct)s, %(net_margin_pct)s,
    %(revenue_growth_pct)s, %(earnings_growth_pct)s,
    %(de_ratio)s, %(ev_ebitda)s
)
ON CONFLICT (ticker) DO UPDATE SET
    fetched_at          = NOW(),
    pe                  = EXCLUDED.pe,
    pb                  = EXCLUDED.pb,
    roe_pct             = EXCLUDED.roe_pct,
    roa_pct             = EXCLUDED.roa_pct,
    eps                 = EXCLUDED.eps,
    gross_margin_pct    = EXCLUDED.gross_margin_pct,
    net_margin_pct      = EXCLUDED.net_margin_pct,
    revenue_growth_pct  = EXCLUDED.revenue_growth_pct,
    earnings_growth_pct = EXCLUDED.earnings_growth_pct,
    de_ratio            = EXCLUDED.de_ratio,
    ev_ebitda           = EXCLUDED.ev_ebitda
"""


def _get(latest, key) -> float | None:
    v = latest.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def fetch_and_upsert(ticker: str) -> dict:
    """Fetch latest annual ratios for `ticker` from KBS and upsert into stock_ratios."""
    from vnstock.api.financial import Finance as VnFinance

    df = VnFinance(symbol=ticker, source='KBS').ratio(period='year', lang='en')
    df = df.set_index('item_id').drop(columns=['item'], errors='ignore')
    latest = df.iloc[:, 0]

    de_pct = _get(latest, 'debt_to_equity')
    row = {
        "ticker":             ticker,
        "pe":                 _get(latest, 'pe_ratio'),
        "pb":                 _get(latest, 'pb_ratio'),
        "roe_pct":            _get(latest, 'roe'),
        "roa_pct":            _get(latest, 'roa'),
        "eps":                _get(latest, 'trailing_eps'),
        "gross_margin_pct":   _get(latest, 'gross_margin'),
        "net_margin_pct":     _get(latest, 'net_margin'),
        "revenue_growth_pct": _get(latest, 'net_revenue'),
        "earnings_growth_pct": _get(latest, 'profit_after_tax_for_shareholders_of_the_parent_company'),
        "de_ratio":           de_pct / 100 if de_pct is not None else None,
        "ev_ebitda":          _get(latest, 'ev_ebitda'),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_SQL, row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch KBS valuation ratios → stock_ratios")
    parser.add_argument("--tickers", default="",
                        help="Comma-separated tickers; empty = get_ratio_tickers()")
    args = parser.parse_args()

    from core.tickers import get_ratio_tickers
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = get_ratio_tickers()

    if not tickers:
        print("No ratio tickers found.")
        sys.exit(1)

    print(f"stock_ratios: {len(tickers)} tickers")
    upserted = 0
    failed = []
    for t in tickers:
        try:
            row = fetch_and_upsert(t)
            upserted += 1
            print(f"  {t}: pe={row['pe']} pb={row['pb']} roe={row['roe_pct']}%")
        except Exception as e:
            print(f"  {t}: FAILED — {e}", file=sys.stderr)
            failed.append(t)
        time.sleep(3.2)  # stay under vnstock 20 req/min rate limit

    print(f"\nTotal: {upserted}/{len(tickers)} upserted")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
