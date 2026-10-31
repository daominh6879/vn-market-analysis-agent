"""
portfolio/financial_gaps.py — which traded tickers miss statements vn-portfolio's long-term engine needs.

Dagster-free (the api container has no dagster): used by pipeline/assets_market_data.financial_gap_sensor and
scripts/probe_financial_sources.py. Periods = VCI (financial_facts, source 'vnstock') ∪ FireAnt
(fireant_financial_data), i.e. what /advisor/market-data serves.
"""
from __future__ import annotations

from datetime import date

GAP_MIN_ANNUAL_YEARS = 4        # long_term.min_annual_years in vn-portfolio
GAP_ANNUAL_WINDOW = 5           # look at the last 5 closed years
GAP_STALE_DAYS = 92 * 2 + 45    # vn-portfolio long_term.stale_financials: latest quarter end older than this
GAP_MAX_TICKERS = 400           # one day's batch; the rest is picked up the next day

_GAP_SQL = """
WITH traded AS (
  SELECT DISTINCT o.ticker FROM ohlcv_daily o JOIN securities s ON s.ticker = o.ticker AND s.is_active
  WHERE o.date >= CURRENT_DATE - 30),
periods AS (
  SELECT ticker, period FROM financial_facts
   WHERE source = 'vnstock' AND report_type = 'consolidated' AND metric_code NOT LIKE 'ratio\\_%%'
     AND ticker IN (SELECT ticker FROM traded)
  UNION
  SELECT ticker, CASE WHEN quarter = 0 THEN year::text ELSE 'Q' || quarter || '/' || year END FROM fireant_financial_data
   WHERE field = ANY(%(fields)s) AND ticker IN (SELECT ticker FROM traded)),
annual AS (
  SELECT ticker, count(*) AS n FROM periods
   WHERE period ~ '^[0-9]{4}$' AND period::int BETWEEN %(y0)s AND %(y1)s GROUP BY ticker),
quarterly AS (
  SELECT ticker, max(make_date(split_part(period, '/', 2)::int, substr(period, 2, 1)::int * 3, 1)
                     + interval '1 month' - interval '1 day')::date AS latest_end
    FROM periods WHERE period ~ '^Q[1-4]/[0-9]{4}$' GROUP BY ticker)
SELECT t.ticker, coalesce(a.n, 0) < %(min_years)s AS annual_gap,
       q.latest_end IS NULL OR q.latest_end < CURRENT_DATE - %(stale_days)s AS quarter_gap
  FROM traded t LEFT JOIN annual a USING (ticker) LEFT JOIN quarterly q USING (ticker)
 ORDER BY t.ticker
"""


def financial_gap_tickers(cur, today: date) -> tuple[list[str], list[str]]:
    """(tickers short of annual years, tickers whose newest quarter is stale) among tickers traded in 30 days."""
    from ingest.fetch_rule_data import FIREANT_TO_VNSTOCK
    cur.execute(_GAP_SQL, {"fields": list(FIREANT_TO_VNSTOCK), "y0": today.year - GAP_ANNUAL_WINDOW,
                           "y1": today.year - 1, "min_years": GAP_MIN_ANNUAL_YEARS, "stale_days": GAP_STALE_DAYS})
    rows = cur.fetchall()
    return [t for t, a, _ in rows if a], [t for t, _, q in rows if q]
