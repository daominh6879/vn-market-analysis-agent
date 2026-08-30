"""
scripts/audit_db.py — Audit DB completeness for agent use.

Checks each ticker has sufficient data across all agent-facing tables.
Prints a summary table + flags gaps.

Usage:
    python scripts/audit_db.py
    python scripts/audit_db.py --tickers HPG,VCB,FPT
    python scripts/audit_db.py --min-ohlcv-days 30 --min-financial-periods 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

# ── Config ─────────────────────────────────────────────────────────────────────

MIN_OHLCV_DAYS = 30          # need at least N trading days
MIN_OHLCV_RECENT_DAYS = 7    # latest date must be within N calendar days
MIN_FINANCIAL_PERIODS = 4    # need at least N periods in financial_facts
MIN_FOREIGN_FLOW_DAYS = 30   # foreign flow days
MIN_NEWS_ARTICLES = 1        # at least 1 news article mentioning ticker
REQUIRED_INDEX_CODES = ["VNINDEX", "HNX", "VN30"]
REQUIRED_QUOTE_CLASSES = ["equity_index", "commodity", "fx"]

# ── Helpers ─────────────────────────────────────────────────────────────────────

def q(sql: str, params=()) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def scalar(sql: str, params=()) -> object:
    rows = q(sql, params)
    return rows[0][0] if rows and rows[0] else None


def get_active_tickers() -> list[str]:
    rows = q("SELECT ticker FROM securities WHERE is_active = TRUE ORDER BY ticker")
    return [r[0] for r in rows]


# ── Per-table checks ────────────────────────────────────────────────────────────

def audit_ohlcv(tickers: list[str], today: date) -> dict[str, dict]:
    """ohlcv_daily: row count + latest date per ticker."""
    rows = q("""
        SELECT ticker, COUNT(*) as cnt, MAX(date) as latest
        FROM ohlcv_daily
        WHERE ticker = ANY(%s)
        GROUP BY ticker
    """, (tickers,))
    result = {r[0]: {"count": r[1], "latest": r[2]} for r in rows}
    return result


def audit_financial_facts(tickers: list[str]) -> dict[str, dict]:
    """financial_facts: distinct periods + sources per ticker."""
    rows = q("""
        SELECT ticker,
               COUNT(DISTINCT period) as periods,
               COUNT(DISTINCT source) as sources,
               array_agg(DISTINCT source) as source_list
        FROM financial_facts
        WHERE ticker = ANY(%s)
        GROUP BY ticker
    """, (tickers,))
    return {r[0]: {"periods": r[1], "sources": r[2], "source_list": list(r[3])} for r in rows}


def audit_foreign_flows(tickers: list[str], today: date) -> dict[str, dict]:
    """foreign_flows: row count + latest date per ticker."""
    rows = q("""
        SELECT ticker, COUNT(*) as cnt, MAX(date) as latest
        FROM foreign_flows
        WHERE ticker = ANY(%s)
        GROUP BY ticker
    """, (tickers,))
    return {r[0]: {"count": r[1], "latest": r[2]} for r in rows}


def audit_news(tickers: list[str]) -> dict[str, int]:
    """news_articles: article count per ticker (via GIN index on tickers array)."""
    rows = q("""
        SELECT t.ticker, COUNT(na.id) as cnt
        FROM unnest(%s::text[]) AS t(ticker)
        LEFT JOIN news_articles na ON na.tickers @> ARRAY[t.ticker]
        GROUP BY t.ticker
    """, (tickers,))
    return {r[0]: r[1] for r in rows}


def audit_securities(tickers: list[str]) -> dict[str, dict]:
    """securities: present, exchange, sector."""
    rows = q("""
        SELECT ticker, exchange, sector, company_name
        FROM securities
        WHERE ticker = ANY(%s)
    """, (tickers,))
    return {r[0]: {"exchange": r[1], "sector": r[2], "company": r[3]} for r in rows}


def audit_market_index(today: date) -> dict:
    """market_index_daily: latest date + row count per index_code."""
    rows = q("""
        SELECT index_code, COUNT(*) as cnt, MAX(date) as latest
        FROM market_index_daily
        GROUP BY index_code
        ORDER BY index_code
    """)
    return {r[0]: {"count": r[1], "latest": r[2]} for r in rows}


def audit_market_quotes(today: date) -> dict:
    """market_quotes: latest date + count per asset_class."""
    rows = q("""
        SELECT asset_class, COUNT(DISTINCT symbol) as symbols, MAX(date) as latest
        FROM market_quotes
        GROUP BY asset_class
        ORDER BY asset_class
    """)
    return {r[0]: {"symbols": r[1], "latest": r[2]} for r in rows}


def audit_corporate_events(tickers: list[str]) -> dict[str, int]:
    """corporate_events: event count per ticker."""
    rows = q("""
        SELECT ticker, COUNT(*) as cnt
        FROM corporate_events
        WHERE ticker = ANY(%s)
        GROUP BY ticker
    """, (tickers,))
    return {r[0]: r[1] for r in rows}


# ── Formatting ──────────────────────────────────────────────────────────────────

PASS = "OK  "
WARN = "WARN"
FAIL = "FAIL"

def status(ok: bool, warn: bool = False) -> str:
    if ok:
        return PASS
    return WARN if warn else FAIL


def days_since(d: date | None, today: date) -> int | None:
    if d is None:
        return None
    return (today - d).days


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", help="Comma-separated tickers (default: all active)")
    parser.add_argument("--min-ohlcv-days", type=int, default=MIN_OHLCV_DAYS)
    parser.add_argument("--min-financial-periods", type=int, default=MIN_FINANCIAL_PERIODS)
    parser.add_argument("--min-foreign-flow-days", type=int, default=MIN_FOREIGN_FLOW_DAYS)
    args = parser.parse_args()

    today = date.today()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = get_active_tickers()

    if not tickers:
        print("No tickers found in securities table.")
        sys.exit(1)

    print(f"\n=== DB Audit — {today} ===")
    print(f"Checking {len(tickers)} tickers: {', '.join(tickers[:20])}{'...' if len(tickers) > 20 else ''}\n")

    # Fetch all audit data
    ohlcv      = audit_ohlcv(tickers, today)
    financials = audit_financial_facts(tickers)
    fflows     = audit_foreign_flows(tickers, today)
    news       = audit_news(tickers)
    securities = audit_securities(tickers)
    corp_ev    = audit_corporate_events(tickers)
    mkt_idx    = audit_market_index(today)
    mkt_q      = audit_market_quotes(today)

    # ── Per-ticker table ──────────────────────────────────────────────────────

    col_w = 8
    header = (
        f"{'Ticker':<10} {'Sec':4} {'OHLCV':6} {'OHLCVd':6} "
        f"{'FinFacts':8} {'FinSrc':6} {'FFlow':5} {'FFlowD':6} "
        f"{'News':4} {'CorpEv':6} {'Status':6}"
    )
    print(header)
    print("-" * len(header))

    gaps: list[str] = []

    for ticker in tickers:
        sec = securities.get(ticker)
        ohl = ohlcv.get(ticker)
        fin = financials.get(ticker)
        ff  = fflows.get(ticker)
        n   = news.get(ticker, 0)
        ce  = corp_ev.get(ticker, 0)

        # securities present
        sec_ok = sec is not None

        # ohlcv
        ohlcv_cnt = ohl["count"] if ohl else 0
        ohlcv_latest = ohl["latest"] if ohl else None
        ohlcv_age = days_since(ohlcv_latest, today)
        ohlcv_ok = ohlcv_cnt >= args.min_ohlcv_days
        ohlcv_fresh = ohlcv_age is not None and ohlcv_age <= MIN_OHLCV_RECENT_DAYS

        # financial_facts
        fin_periods = fin["periods"] if fin else 0
        fin_sources = fin["source_list"] if fin else []
        fin_ok = fin_periods >= args.min_financial_periods

        # foreign flows
        ff_cnt = ff["count"] if ff else 0
        ff_latest = ff["latest"] if ff else None
        ff_age = days_since(ff_latest, today)
        ff_ok = ff_cnt >= args.min_foreign_flow_days

        # overall
        ticker_ok = sec_ok and ohlcv_ok and ohlcv_fresh and fin_ok and ff_ok
        ticker_warn = sec_ok and ohlcv_ok and not (ohlcv_fresh and fin_ok and ff_ok)

        row_status = PASS if ticker_ok else (WARN if ticker_warn else FAIL)

        fin_src_str = "+".join(fin_sources) if fin_sources else "none"

        print(
            f"{ticker:<10} "
            f"{'Y' if sec_ok else 'N':4} "
            f"{ohlcv_cnt:<6} "
            f"{str(ohlcv_age)+'d' if ohlcv_age is not None else 'N/A':<6} "
            f"{fin_periods:<8} "
            f"{fin_src_str:<6} "
            f"{ff_cnt:<5} "
            f"{str(ff_age)+'d' if ff_age is not None else 'N/A':<6} "
            f"{n:<4} "
            f"{ce:<6} "
            f"{row_status}"
        )

        if row_status != PASS:
            issues = []
            if not sec_ok:              issues.append("missing in securities")
            if not ohlcv_ok:            issues.append(f"ohlcv only {ohlcv_cnt} rows (need {args.min_ohlcv_days})")
            if not ohlcv_fresh:         issues.append(f"ohlcv stale ({ohlcv_age}d old)")
            if not fin_ok:              issues.append(f"financial_facts only {fin_periods} periods (need {args.min_financial_periods})")
            if not ff_ok:               issues.append(f"foreign_flows only {ff_cnt} rows (need {args.min_foreign_flow_days})")
            gaps.append(f"  {ticker}: {'; '.join(issues)}")

    # ── Market-wide tables ────────────────────────────────────────────────────

    print(f"\n--- market_index_daily ---")
    missing_idx = []
    for code in REQUIRED_INDEX_CODES:
        info = mkt_idx.get(code)
        if info:
            age = days_since(info["latest"], today)
            st = PASS if age <= MIN_OHLCV_RECENT_DAYS else WARN
            print(f"  {code:<12} rows={info['count']:<6} latest={info['latest']} ({age}d ago) [{st}]")
        else:
            print(f"  {code:<12} MISSING [FAIL]")
            missing_idx.append(code)

    print(f"\n--- market_quotes ---")
    missing_cls = []
    for cls in REQUIRED_QUOTE_CLASSES:
        info = mkt_q.get(cls)
        if info:
            age = days_since(info["latest"], today)
            st = PASS if age <= MIN_OHLCV_RECENT_DAYS else WARN
            print(f"  {cls:<16} symbols={info['symbols']:<4} latest={info['latest']} ({age}d ago) [{st}]")
        else:
            print(f"  {cls:<16} MISSING [FAIL]")
            missing_cls.append(cls)

    # ── news_articles global stats ────────────────────────────────────────────
    total_news = scalar("SELECT COUNT(*) FROM news_articles")
    indexed_news = scalar("SELECT COUNT(*) FROM news_articles WHERE indexed_at IS NOT NULL")
    latest_news = scalar("SELECT MAX(published_at)::date FROM news_articles")
    print(f"\n--- news_articles ---")
    print(f"  total={total_news}  indexed={indexed_news}  latest={latest_news}")

    # ── Summary ───────────────────────────────────────────────────────────────

    print(f"\n=== GAPS ({len(gaps)}) ===")
    if gaps:
        for g in gaps:
            print(g)
    else:
        print("  None — all tickers look good.")

    if missing_idx:
        print(f"\nMissing index codes: {missing_idx}")
    if missing_cls:
        print(f"Missing quote classes: {missing_cls}")

    fail_count = sum(1 for g in gaps if "FAIL" in g or True)  # gaps = any issue
    print(f"\nResult: {len(tickers) - len(gaps)}/{len(tickers)} tickers fully OK")

    sys.exit(1 if gaps or missing_idx or missing_cls else 0)


if __name__ == "__main__":
    main()
