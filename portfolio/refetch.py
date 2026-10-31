"""
portfolio/refetch.py — on-demand re-fetch of a few tickers for vn-portfolio's "Dữ liệu thiếu" page (/advisor/refetch).

Not a new dataset: it re-runs, for the named tickers only, the same fetch + upsert functions the scheduled Dagster
assets use (vnstock_financials, financials_quarterly, ohlcv_daily_ingest), so a ticker whose statements are missing or
stale can be filled without waiting for the next monthly / weekly run. Every write is an idempotent upsert into the
existing tables (financial_facts, ohlcv_daily — the tables /advisor/market-data reads).

Each result says what actually changed: periods / sessions held before and after, and the newest one, so "fetched
412 rows" that only rewrote the same years is reported as no new period (status "unchanged").

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
    """ohlcv_daily (what /advisor/market-data serves), same function as the ohlcv_daily_ingest asset: since the
    latest stored session, or PRICE_DAYS_BACK days for a ticker without rows."""
    from ingest.fetch_ohlcv import fetch_and_upsert
    return int((fetch_and_upsert(ticker, days=PRICE_DAYS_BACK) or {}).get("ohlcv") or 0)


FETCHERS = {"financials_annual": _annual, "financials_quarterly": _quarterly, "prices": _prices}

# what /advisor/market-data reads, per dataset: (count, newest) of distinct periods / sessions for one ticker
_FIN = ("FROM financial_facts WHERE ticker = %s AND source = 'vnstock' AND report_type = 'consolidated' "
        "AND period ~ %s")
STATE_SQL = {
    "financials_annual": (f"SELECT COUNT(DISTINCT period), MAX(period) {_FIN}", r"^[0-9]{4}$"),
    "financials_quarterly": (f"SELECT COUNT(DISTINCT period), "
                             f"MAX(split_part(period, '/', 2) || '-Q' || substr(period, 2, 1)) {_FIN}",
                             r"^Q[1-4]/[0-9]{4}$"),
    "prices": ("SELECT COUNT(*), MAX(date)::text FROM ohlcv_daily WHERE ticker = %s", None),
}


def state(ticker: str, dataset: str) -> dict:
    """{count, latest} of what the engine side will see for this ticker × dataset."""
    from core.db import get_conn
    sql, pattern = STATE_SQL[dataset]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, pattern) if pattern else (ticker,))
        n, latest = cur.fetchone()
    return {"count": int(n or 0), "latest": latest}


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


STATUSES = ("new", "unchanged", "empty", "error")


def refetch(tickers: list[str], datasets: list[str]) -> dict:
    """{results: [{ticker, dataset, status, rows, before, after, error?, seconds}], new, unchanged, empty, error}.
    new = more periods / sessions or a newer latest one than before; unchanged = the source answered but holds
    nothing the table did not have; empty = the source returned nothing; rows = rows upserted (incl. rewrites)."""
    ts, ds = validate(tickers, datasets)
    results = []
    for t in ts:
        for d in ds:
            t0 = time.time()
            r = {"ticker": t, "dataset": d, "rows": 0}
            try:
                r["before"] = state(t, d)
                r["rows"] = FETCHERS[d](t)
                r["after"] = state(t, d)
                b, a = r["before"], r["after"]
                grew = a["count"] > b["count"] or (a["latest"] or "") > (b["latest"] or "")
                r["status"] = "new" if grew else "unchanged" if r["rows"] else "empty"
            except (Exception, SystemExit) as exc:          # fetchers sys.exit on some source errors
                r.update(status="error", error=f"{type(exc).__name__}: {exc}"[:200])
            r["seconds"] = round(time.time() - t0, 1)
            results.append(r)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    return {"results": results, **counts}
