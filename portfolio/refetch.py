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


FIREANT_QUARTERS = 60        # same depth as fireant_data_ingest: 15 years of quarters, 17 years of annual values


def _vci(ticker: str, quarterly: bool) -> int:
    """VCI statements → financial_facts (vnstock_financials / financials_quarterly assets)."""
    from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts
    year = date.today().year
    back = QUARTERLY_YEARS_BACK if quarterly else ANNUAL_YEARS_BACK
    facts = fetch_finance_facts(ticker=ticker, report_type="consolidated", period_from=year - back, period_to=year,
                                source="VCI", quarterly=quarterly)
    return insert_vnstock_facts(facts) if facts else 0


def _fireant(ticker: str) -> int:
    """FireAnt quarterly + annual values → fireant_financial_data (fireant_data_ingest asset). /advisor/market-data
    fills from them every period and field VCI lacks. 0 when no FireAnt credentials are configured."""
    from vnmarket import VnMarket
    from vnmarket.errors import NoData
    from vnmarket.sources import fireant
    from ingest.fetch_rule_data import fireant_data_one
    if not fireant.enabled():
        raise RuntimeError("FireAnt chưa cấu hình (FIREANT_TOKEN hoặc FIREANT_EMAIL/PASSWORD)")
    with VnMarket() as vn:
        try:
            return fireant_data_one(vn, ticker, FIREANT_QUARTERS)
        except NoData:
            return 0


def _financials(ticker: str, quarterly: bool, fireant_done: dict) -> tuple[int, dict]:
    """Both statement sources, each one's outcome reported; raises only when every source failed. FireAnt answers
    quarters and years in one call, so it runs once per ticker per request (fireant_done: ticker → rows or error);
    the second dataset reuses that outcome."""
    rows, sources = 0, {}
    try:
        n = _vci(ticker, quarterly)
        rows += n
        sources["vci"] = n
    except (Exception, SystemExit) as exc:              # fetchers sys.exit on some source errors
        sources["vci"] = f"lỗi: {type(exc).__name__}: {exc}"[:160]
    if ticker not in fireant_done:
        try:
            n = _fireant(ticker)
            rows += n
            fireant_done[ticker] = n
        except (Exception, SystemExit) as exc:
            fireant_done[ticker] = f"lỗi: {type(exc).__name__}: {exc}"[:160]
        sources["fireant"] = fireant_done[ticker]
    else:
        prev = fireant_done[ticker]
        sources["fireant"] = prev if isinstance(prev, str) else f"đã tải cùng lần này ({prev} dòng)"
    if all(isinstance(v, str) and v.startswith("lỗi") for v in sources.values()):
        raise RuntimeError("; ".join(f"{k} {v}" for k, v in sources.items()))
    return rows, sources


def _annual(ticker: str, fireant_done: dict | None = None) -> tuple[int, dict]:
    return _financials(ticker, False, {} if fireant_done is None else fireant_done)


def _quarterly(ticker: str, fireant_done: dict | None = None) -> tuple[int, dict]:
    return _financials(ticker, True, {} if fireant_done is None else fireant_done)


def _prices(ticker: str, _fireant_done: dict | None = None) -> int:
    """ohlcv_daily (what /advisor/market-data serves), same function as the ohlcv_daily_ingest asset: since the
    latest stored session, or PRICE_DAYS_BACK days for a ticker without rows."""
    from ingest.fetch_ohlcv import fetch_and_upsert
    return int((fetch_and_upsert(ticker, days=PRICE_DAYS_BACK) or {}).get("ohlcv") or 0)


FETCHERS = {"financials_annual": _annual, "financials_quarterly": _quarterly, "prices": _prices}

# what /advisor/market-data serves, per dataset: (count, newest) of distinct periods / sessions for one ticker —
# financial periods = vnstock (VCI) periods ∪ FireAnt periods (market_data._fireant_history fills the gaps)
_PERIODS = """SELECT period AS p FROM financial_facts WHERE ticker = %(t)s AND source = 'vnstock'
                AND report_type = 'consolidated' AND period ~ %(pattern)s
              UNION SELECT {fa_period} FROM fireant_financial_data WHERE ticker = %(t)s AND {fa_where}
                AND field = ANY(%(fields)s)"""
STATE_SQL = {
    "financials_annual": ("SELECT COUNT(*), MAX(p) FROM ({}) x".format(
        _PERIODS.format(fa_period="year::text", fa_where="quarter = 0")), r"^[0-9]{4}$"),
    "financials_quarterly": ("SELECT COUNT(*), MAX(split_part(p, '/', 2) || '-Q' || substr(p, 2, 1)) FROM ({}) x".format(
        _PERIODS.format(fa_period="'Q' || quarter || '/' || year", fa_where="quarter BETWEEN 1 AND 4")),
        r"^Q[1-4]/[0-9]{4}$"),
    "prices": ("SELECT COUNT(*), MAX(date)::text FROM ohlcv_daily WHERE ticker = %(t)s", None),
}


def state(ticker: str, dataset: str) -> dict:
    """{count, latest} of what the engine side will see for this ticker × dataset."""
    from core.db import get_conn
    from ingest.fetch_rule_data import FIREANT_TO_VNSTOCK
    sql, pattern = STATE_SQL[dataset]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, {"t": ticker, "pattern": pattern, "fields": list(FIREANT_TO_VNSTOCK)})
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
    fireant_done: dict[str, int | str] = {}
    for t in ts:
        # every dataset's "before" first: FireAnt, run with the first financial dataset, also brings the other one
        before = {}
        for d in ds:
            try:
                before[d] = state(t, d)
            except Exception as exc:
                before[d] = exc
        rs = []
        for d in ds:
            t0 = time.time()
            r = {"ticker": t, "dataset": d, "rows": 0}
            try:
                if isinstance(before[d], Exception):
                    raise before[d]
                r["before"] = before[d]
                out = FETCHERS[d](t, fireant_done)
                r["rows"], r["sources"] = out if isinstance(out, tuple) else (out, None)
            except (Exception, SystemExit) as exc:          # fetchers sys.exit on some source errors
                r.update(status="error", error=f"{type(exc).__name__}: {exc}"[:200])
            r["seconds"] = round(time.time() - t0, 1)
            rs.append(r)
        for r in rs:
            if r.get("status") == "error":
                continue
            try:
                r["after"] = state(t, r["dataset"])
            except Exception as exc:
                r.update(status="error", error=f"{type(exc).__name__}: {exc}"[:200])
                continue
            b, a = r["before"], r["after"]
            grew = a["count"] > b["count"] or (a["latest"] or "") > (b["latest"] or "")
            fetched = r["rows"] or any(isinstance(v, int) and v or isinstance(v, str) and v.startswith("đã tải")
                                       for v in (r.get("sources") or {}).values())
            r["status"] = "new" if grew else "unchanged" if fetched else "empty"
        results += rs
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    return {"results": results, **counts}
