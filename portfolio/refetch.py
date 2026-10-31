"""
portfolio/refetch.py — on-demand re-fetch of a few tickers for vn-portfolio's "Dữ liệu thiếu" page (/advisor/refetch).

Not a new dataset: it re-runs, for the named tickers only, the same fetch + upsert functions the scheduled Dagster
assets use (vnstock_financials, financials_quarterly, vnstock_prices), so a ticker whose statements are missing or
stale can be filled without waiting for the next monthly / weekly run. Every write is an idempotent upsert into the
existing tables (financial_facts, stock_prices).

Bounded: MAX_TICKERS per call (the caller batches), each ticker × dataset reported separately; one failure never
stops the others.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

DATASETS = ("financials_annual", "financials_quarterly", "prices")
MAX_TICKERS = 10
ANNUAL_YEARS_BACK = 8        # LT engine needs ≥ 4 consecutive annual years, prefers 5; gaps break the series
QUARTERLY_YEARS_BACK = 2     # same window as the financials_quarterly asset
PRICE_DAYS_BACK = 400        # covers price_history<60 / 52-week windows for a newly listed ticker
TICKER = re.compile(r"^[A-Z0-9]{3,10}$")


def _annual(ticker: str) -> int:
    from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts
    year = date.today().year
    facts = fetch_finance_facts(ticker=ticker, report_type="consolidated", period_from=year - ANNUAL_YEARS_BACK,
                                period_to=year, source="VCI", quarterly=False)
    return insert_vnstock_facts(facts) if facts else 0


def _quarterly(ticker: str) -> int:
    from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts
    year = date.today().year
    facts = fetch_finance_facts(ticker=ticker, report_type="consolidated", period_from=year - QUARTERLY_YEARS_BACK,
                                period_to=year, source="KBS", quarterly=True)
    return insert_vnstock_facts(facts) if facts else 0


def _prices(ticker: str) -> int:
    from ingest.fetch_prices import fetch_and_insert
    today = date.today()
    return fetch_and_insert(ticker, (today - timedelta(days=PRICE_DAYS_BACK)).isoformat(), today.isoformat())


FETCHERS = {"financials_annual": _annual, "financials_quarterly": _quarterly, "prices": _prices}


def validate(tickers: list[str], datasets: list[str]) -> tuple[list[str], list[str]]:
    """Normalized (tickers, datasets); ValueError on an empty / oversized / unknown request."""
    ts = list(dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip()))
    ds = list(dict.fromkeys(datasets))
    if not ts:
        raise ValueError("no tickers")
    if len(ts) > MAX_TICKERS:
        raise ValueError(f"at most {MAX_TICKERS} tickers per call")
    bad = [t for t in ts if not TICKER.match(t)]
    if bad:
        raise ValueError(f"invalid tickers: {bad}")
    unknown = [d for d in ds if d not in FETCHERS]
    if not ds or unknown:
        raise ValueError(f"datasets must be a non-empty subset of {list(DATASETS)}; unknown: {unknown}")
    return ts, ds


def refetch(tickers: list[str], datasets: list[str]) -> dict:
    """{results: [{ticker, dataset, status ok|empty|error, rows, error?, seconds}], ok, empty, error}.
    'empty' = the source returned nothing (prices: already up to date also returns 0 rows)."""
    ts, ds = validate(tickers, datasets)
    results = []
    for t in ts:
        for d in ds:
            t0 = time.time()
            try:
                n = FETCHERS[d](t)
                results.append({"ticker": t, "dataset": d, "status": "ok" if n else "empty", "rows": n,
                                "seconds": round(time.time() - t0, 1)})
            except (Exception, SystemExit) as exc:          # fetchers sys.exit on some source errors
                results.append({"ticker": t, "dataset": d, "status": "error", "rows": 0,
                                "error": f"{type(exc).__name__}: {exc}"[:200], "seconds": round(time.time() - t0, 1)})
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("ok", "empty", "error")}
    return {"results": results, **counts}
