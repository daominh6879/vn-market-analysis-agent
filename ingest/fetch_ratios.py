"""
ingest/fetch_ratios.py — Fetch KBS valuation ratios → stock_ratios.

Single-command daily refresh of P/E, P/B, ROE, EPS… for VN30+VN100 tickers.
Mirrors the Dagster asset vnstock_ratios (pipeline/assets_vnstock.py).

Usage:
    python ingest/fetch_ratios.py                 # every active security (get_tickers)
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


def _reprice(ticker: str, fiscal_year: int | None, pe, pb, eps):
    """Re-express KBS year-end valuation at today's adjusted price.

    KBS pe/pb are computed at the fiscal year-end price and eps is the raw (pre-dilution)
    figure. With ref = adjusted close on the last session <= Dec 31 and now = latest close:
        eps_adj = ref / pe          (so a later stock dividend/split is reflected)
        pe_live = pe * now / ref    (= now / eps_adj)
        pb_live = pb * now / ref
    Returns (pe, pb, eps) unchanged if prices are unavailable.
    """
    if not fiscal_year or not pe or pe <= 0:
        return pe, pb, eps
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT close FROM ohlcv_daily WHERE ticker=%s AND date <= %s "
                            "ORDER BY date DESC LIMIT 1", (ticker, f"{fiscal_year}-12-31"))
                ref = cur.fetchone()
                cur.execute("SELECT close FROM ohlcv_daily WHERE ticker=%s ORDER BY date DESC LIMIT 1",
                            (ticker,))
                now = cur.fetchone()
        if not ref or not now or not ref[0] or float(ref[0]) <= 0:
            return pe, pb, eps
        ref_p, now_p = float(ref[0]) * 1000.0, float(now[0]) * 1000.0  # nghìn đồng → VND
        scale = now_p / ref_p
        return round(pe * scale, 2), (round(pb * scale, 2) if pb else pb), round(ref_p / pe, 2)
    except Exception:
        return pe, pb, eps


def fetch_and_upsert(ticker: str) -> dict:
    """Fetch latest annual ratios for `ticker` from KBS and upsert into stock_ratios."""
    from vnstock.api.financial import Finance as VnFinance

    df = VnFinance(symbol=ticker, source='KBS').ratio(period='year', lang='en')
    df = df.set_index('item_id').drop(columns=['item'], errors='ignore')
    latest = df.iloc[:, 0]
    try:
        fiscal_year = int(str(latest.name)[:4])
    except (TypeError, ValueError):
        fiscal_year = None

    de_pct = _get(latest, 'debt_to_equity')
    pe, pb, eps = _reprice(ticker, fiscal_year, _get(latest, 'pe_ratio'),
                           _get(latest, 'pb_ratio'), _get(latest, 'trailing_eps'))
    row = {
        "ticker":             ticker,
        "pe":                 pe,
        "pb":                 pb,
        "roe_pct":            _get(latest, 'roe'),
        "roa_pct":            _get(latest, 'roa'),
        "eps":                eps,
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


# KBS ratio item_id → financial_facts.metric_code for the per-year history (ROE 2020 vs 2024, EPS 2023...).
_HISTORY_METRICS = {
    "roe": "ratio_roe_pct",
    "roa": "ratio_roa_pct",
    "trailing_eps": "ratio_eps",
    "pe_ratio": "ratio_pe",
    "pb_ratio": "ratio_pb",
    "gross_margin": "ratio_gross_margin_pct",
    "net_margin": "ratio_net_margin_pct",
}


def ratio_history_facts(ticker: str, df) -> list[dict]:
    """KBS ratio frame (rows=item_id, cols=years) → financial_facts rows, one per (year, ratio).

    Values are as published for that fiscal year (EPS is NOT adjusted for later splits/stock
    dividends; P/E and P/B are at that year's closing price) — the stock_ratios snapshot is the
    re-priced one.
    """
    import re
    df = df.set_index("item_id").drop(columns=["item"], errors="ignore")
    facts: list[dict] = []
    for col in df.columns:
        m = re.match(r"(\d{4})(?:-\S+)?$", str(col))   # KBS labels annual columns '2025-Năm'
        if not m:
            continue
        year = m.group(1)
        for item_id, metric in _HISTORY_METRICS.items():
            if item_id not in df.index:
                continue
            v = _get(df[col], item_id)
            if v is None:
                continue
            facts.append(dict(
                ticker=ticker, period=year, report_type="consolidated", metric_code=metric,
                value=v, unit="VND" if metric == "ratio_eps" else ("x" if metric in ("ratio_pe", "ratio_pb") else "%"),
                source_file="vnstock_kbs_ratio", source_page=0, source="vnstock_ratio",
            ))
    return facts


def fetch_and_upsert_history(ticker: str) -> int:
    from vnstock.api.financial import Finance as VnFinance
    from ingest.fetch_financials import insert_vnstock_facts

    df = VnFinance(symbol=ticker, source="KBS").ratio(period="year", lang="en")
    facts = ratio_history_facts(ticker, df)
    return insert_vnstock_facts(facts) if facts else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch KBS valuation ratios → stock_ratios")
    parser.add_argument("--tickers", default="",
                        help="Comma-separated tickers; empty = every active security")
    parser.add_argument("--history", action="store_true",
                        help="Store every fiscal year's ROE/ROA/EPS/PE/PB/margins in financial_facts "
                             "(metric_code ratio_*) instead of refreshing the stock_ratios snapshot")
    args = parser.parse_args()

    from core.tickers import get_tickers
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = get_tickers()

    if not tickers:
        print("No ratio tickers found.")
        sys.exit(1)

    print(f"{'ratio history' if args.history else 'stock_ratios'}: {len(tickers)} tickers")
    upserted = 0
    failed = []
    for t in tickers:
        try:
            if args.history:
                n = fetch_and_upsert_history(t)
                if n == 0:
                    raise ValueError("no ratio history returned")
                upserted += 1
                print(f"  {t}: {n} yearly ratio facts")
                time.sleep(3.2)
                continue
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
