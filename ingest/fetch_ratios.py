"""
ingest/fetch_ratios.py — Fetch valuation ratios via vnmarket → stock_ratios.

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


def _ratio(latest, key, scale: float = 1.0) -> float | None:
    """vnmarket reports 0.0 for a ratio that does not apply (bank-only ratios on non-banks, etc.)."""
    v = _get(latest, key)
    return None if not v else round(v * scale, 4)


def _eps(latest) -> float | None:
    """EPS implied by the ratio frame: price (market_cap / shares) / pe. vnmarket has no EPS field."""
    pe, cap, shares = _get(latest, "pe"), _get(latest, "market_cap"), _get(latest, "number_of_shares_mkt_cap")
    if not pe or pe <= 0 or not cap or not shares:
        return None
    return round(cap / shares / pe, 2)


def _latest_ttm(df):
    """Newest TTM row of the vnmarket ratio frame (priced at today's market cap)."""
    rows = df[df["ratio_type"] == "RATIO_TTM"] if "ratio_type" in df.columns else df
    rows = (rows if not rows.empty else df).sort_values(["year", "quarter"])
    return rows.iloc[-1]


def _growth_pct(vn, ticker: str) -> tuple[float | None, float | None]:
    """YoY growth (%) of net revenue and parent net profit from the last two annual statements."""
    try:
        df = vn.finance.statement(ticker, kind="income", period="year", labels=False).sort_values("year")
    except Exception:
        return None, None

    def g(col):
        s = df[col].dropna() if col in df.columns else df.iloc[0:0]
        return round((float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100, 2) if len(s) >= 2 and s.iloc[-2] > 0 else None

    return g("isa3"), g("isa22")


def ratio_row(ticker: str) -> dict:
    """Latest valuation/profitability snapshot for `ticker` via vnmarket (no DB access).

    pe/pb/ev_ebitda/roe/roa/margins are TTM and already priced at today's market cap, so no
    re-pricing is needed. Percent fields are x100; de_ratio is a plain ratio.
    """
    from core.vnmarket_client import get_client
    vn = get_client()
    latest = _latest_ttm(vn.finance.ratios(ticker))
    revenue_growth, earnings_growth = _growth_pct(vn, ticker)
    return {
        "ticker":              ticker,
        "pe":                  _ratio(latest, "pe"),
        "pb":                  _ratio(latest, "pb"),
        "roe_pct":             _ratio(latest, "roe", 100),
        "roa_pct":             _ratio(latest, "roa", 100),
        "eps":                 _eps(latest),
        "gross_margin_pct":    _ratio(latest, "gross_margin", 100),
        "net_margin_pct":      _ratio(latest, "after_tax_profit_margin", 100),
        "revenue_growth_pct":  revenue_growth,
        "earnings_growth_pct": earnings_growth,
        "de_ratio":            _ratio(latest, "debt_per_equity"),
        "ev_ebitda":           _ratio(latest, "ev_to_ebitda"),
    }


def fetch_and_upsert(ticker: str) -> dict:
    """Fetch the latest ratios for `ticker` via vnmarket and upsert into stock_ratios."""
    row = ratio_row(ticker)
    if all(row[k] is None for k in ("pe", "pb", "roe_pct", "eps")):
        raise ValueError("vnmarket returned no ratios")   # a run that produces nothing must fail
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_SQL, row)
    return row


# vnmarket ratio column → (financial_facts.metric_code, scale) for the per-year history (ROE 2020 vs 2024, EPS 2023...).
_HISTORY_METRICS = {
    "roe": ("ratio_roe_pct", 100),
    "roa": ("ratio_roa_pct", 100),
    "pe": ("ratio_pe", 1),
    "pb": ("ratio_pb", 1),
    "gross_margin": ("ratio_gross_margin_pct", 100),
    "after_tax_profit_margin": ("ratio_net_margin_pct", 100),
}


def ratio_history_facts(ticker: str, df) -> list[dict]:
    """vnmarket ratio frame → financial_facts rows, one per (fiscal year, ratio).

    Uses the RATIO_YEAR rows. Values are as of that fiscal year-end (EPS = year-end price / P/E,
    NOT adjusted for later splits/stock dividends) — the stock_ratios snapshot is the current one.
    """
    rows = df[df["ratio_type"] == "RATIO_YEAR"] if "ratio_type" in df.columns else df.iloc[0:0]
    facts: list[dict] = []
    for _, r in rows.iterrows():
        try:
            year = str(int(r["year_report"]))
        except (TypeError, ValueError):
            continue
        values = {metric: _ratio(r, col, scale) for col, (metric, scale) in _HISTORY_METRICS.items()}
        values["ratio_eps"] = _eps(r)
        for metric, v in values.items():
            if v is None:
                continue
            facts.append(dict(
                ticker=ticker, period=year, report_type="consolidated", metric_code=metric,
                value=v, unit="VND" if metric == "ratio_eps" else ("x" if metric in ("ratio_pe", "ratio_pb") else "%"),
                source_file="vnmarket_ratio", source_page=0, source="vnstock_ratio",
            ))
    return facts


def fetch_and_upsert_history(ticker: str) -> int:
    from core.vnmarket_client import get_client
    from ingest.fetch_financials import insert_vnstock_facts

    facts = ratio_history_facts(ticker, get_client().finance.ratios(ticker))
    return insert_vnstock_facts(facts) if facts else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch valuation ratios via vnmarket → stock_ratios")
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
                continue
            row = fetch_and_upsert(t)
            upserted += 1
            print(f"  {t}: pe={row['pe']} pb={row['pb']} roe={row['roe_pct']}%")
        except Exception as e:
            print(f"  {t}: FAILED — {e}", file=sys.stderr)
            failed.append(t)

    print(f"\nTotal: {upserted}/{len(tickers)} upserted")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
