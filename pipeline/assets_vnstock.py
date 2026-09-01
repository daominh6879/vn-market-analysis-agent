"""
pipeline/assets_vnstock.py — Dagster assets for vnstock data ingestion.

Assets:
  vnstock_financials — fetch annual financial statements → Postgres financial_facts
  vnstock_prices     — fetch daily stock prices → Postgres stock_prices
  vnstock_ratios     — fetch KBS valuation ratios → Postgres stock_ratios (daily)

Schedules:
  vnstock_financials_schedule — 0 1 1 * *    (1st of month, 01:00)
  vnstock_prices_schedule     — 0 18 * * 1-5  (weekdays 18:00, after HoSE close)
  vnstock_ratios_schedule     — 30 18 * * 1-5 (weekdays 18:30, after prices)
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, Config, RetryPolicy, ScheduleDefinition, asset, define_asset_job


# ── Config ─────────────────────────────────────────────────────────────────────

class VnstockFinancialsConfig(Config):
    tickers: str = ""   # empty = all active tickers from securities table
    period_from: int = 2020
    period_to: int = date.today().year
    report_type: str = "consolidated"
    source: str = "VCI"


class VnstockPricesConfig(Config):
    tickers: str = ""   # empty = all active tickers from securities table
    days_back: int = 2  # fetch last N days (idempotent via ON CONFLICT)


# ── Assets ─────────────────────────────────────────────────────────────────────

@asset(
    group_name="vnstock",
    description="Fetch annual financial statements from vnstock → Postgres financial_facts (source='vnstock'). Upserts — safe to re-run.",
    retry_policy=RetryPolicy(max_retries=3, delay=60),
)
def vnstock_financials(context: AssetExecutionContext, config: VnstockFinancialsConfig) -> dict:
    import time
    from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts
    from core.tickers import get_tickers

    ticker_list = [t.strip().upper() for t in config.tickers.split(",") if t.strip()] or get_tickers()
    total_facts = 0
    failed: list[str] = []

    for ticker in ticker_list:
        context.log.info(f"Fetching financials: {ticker} ({config.period_from}–{config.period_to})")
        try:
            facts = fetch_finance_facts(
                ticker=ticker,
                report_type=config.report_type,
                period_from=config.period_from,
                period_to=config.period_to,
                source=config.source,
            )
            if facts:
                n = insert_vnstock_facts(facts)
                total_facts += n
                context.log.info(f"  {ticker}: {n} facts upserted")
            else:
                context.log.warning(f"  {ticker}: 0 facts returned")
        except Exception as exc:
            context.log.error(f"  {ticker} FAILED: {exc}")
            failed.append(ticker)
        time.sleep(1.5)

    if failed:
        context.log.warning(f"vnstock_financials: {len(failed)} tickers failed: {failed}")
    context.log.info(f"vnstock_financials done: {total_facts} total facts")
    return {"total_facts": total_facts, "tickers": ticker_list, "failed": failed}


@asset(
    group_name="vnstock",
    description="Fetch daily stock prices from vnstock → Postgres stock_prices. Upserts — safe to re-run.",
    retry_policy=RetryPolicy(max_retries=3, delay=30),
)
def vnstock_prices(context: AssetExecutionContext, config: VnstockPricesConfig) -> dict:
    import time
    from ingest.fetch_prices import fetch_and_insert
    from core.tickers import get_tickers

    ticker_list = [t.strip().upper() for t in config.tickers.split(",") if t.strip()] or get_tickers()
    today = date.today()
    from_date = str(today - timedelta(days=config.days_back))
    to_date = str(today)
    total_rows = 0
    failed: list[str] = []

    for ticker in ticker_list:
        context.log.info(f"Fetching prices: {ticker} ({from_date} → {to_date})")
        try:
            n = fetch_and_insert(ticker, from_date, to_date)
            total_rows += n
            context.log.info(f"  {ticker}: {n} rows upserted")
        except Exception as exc:
            context.log.error(f"  {ticker} FAILED: {exc}")
            failed.append(ticker)
        time.sleep(1.1)

    if failed:
        context.log.warning(f"vnstock_prices: {len(failed)} tickers failed: {failed}")
    context.log.info(f"vnstock_prices done: {total_rows} total rows")
    return {"total_rows": total_rows, "tickers": ticker_list, "failed": failed, "date_range": f"{from_date}→{to_date}"}


# ── Ratios asset ───────────────────────────────────────────────────────────────

# All tickers the fundamentals intent may need for peer comparison
_RATIO_TICKERS = [
    "VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "STB",  # banking
    "HPG", "HSG", "NKG", "TLH",                                # steel
    "FPT", "CMG", "VGI",                                       # tech
    "VHM", "VIC", "NVL", "PDR", "DXG",                        # real estate
]

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
    ev_ebitda           = EXCLUDED.ev_ebitda;
"""


@asset(
    group_name="vnstock",
    description="Fetch KBS valuation ratios (P/E, P/B, ROE, EPS…) → Postgres stock_ratios. Daily upsert.",
    retry_policy=RetryPolicy(max_retries=2, delay=120),
)
def vnstock_ratios(context: AssetExecutionContext) -> dict:
    import math
    import time
    from vnstock.api.financial import Finance as VnFinance
    from core.db import get_conn

    upserted = 0
    failed: list[str] = []

    for ticker in _RATIO_TICKERS:
        context.log.info(f"Fetching ratios: {ticker}")
        try:
            df = VnFinance(symbol=ticker, source='KBS').ratio(period='year', lang='en')
            df = df.set_index('item_id').drop(columns=['item'], errors='ignore')
            latest = df.iloc[:, 0]

            def _get(key) -> float | None:
                v = latest.get(key)
                if v is None:
                    return None
                try:
                    f = float(v)
                    return None if math.isnan(f) else f
                except (TypeError, ValueError):
                    return None

            roe_pct = _get('roe')
            de_pct  = _get('debt_to_equity')
            row = {
                "ticker":             ticker,
                "pe":                 _get('pe_ratio'),
                "pb":                 _get('pb_ratio'),
                "roe_pct":            roe_pct,
                "roa_pct":            _get('roa'),
                "eps":                _get('trailing_eps'),
                "gross_margin_pct":   _get('gross_margin'),
                "net_margin_pct":     _get('net_margin'),
                "revenue_growth_pct": _get('net_revenue'),
                "earnings_growth_pct": _get('profit_after_tax_for_shareholders_of_the_parent_company'),
                "de_ratio":           de_pct / 100 if de_pct is not None else None,
                "ev_ebitda":          _get('ev_ebitda'),
            }
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(_UPSERT_SQL, row)
            upserted += 1
            context.log.info(f"  {ticker}: upserted (pe={row['pe']}, pb={row['pb']}, roe={row['roe_pct']}%)")
        except Exception as exc:
            context.log.error(f"  {ticker} FAILED: {exc}")
            failed.append(ticker)

        time.sleep(2.0)  # KBS rate limit guard

    if failed:
        context.log.warning(f"vnstock_ratios: {len(failed)} failed: {failed}")
    context.log.info(f"vnstock_ratios done: {upserted}/{len(_RATIO_TICKERS)} upserted")
    return {"upserted": upserted, "failed": failed}


# ── Jobs ───────────────────────────────────────────────────────────────────────

vnstock_financials_job = define_asset_job(
    name="vnstock_financials_job",
    selection=[vnstock_financials],
)

vnstock_prices_job = define_asset_job(
    name="vnstock_prices_job",
    selection=[vnstock_prices],
)

vnstock_ratios_job = define_asset_job(
    name="vnstock_ratios_job",
    selection=[vnstock_ratios],
)


# ── Schedules ──────────────────────────────────────────────────────────────────

vnstock_financials_schedule = ScheduleDefinition(
    job=vnstock_financials_job,
    cron_schedule="0 1 1 * *",           # 1st of month 01:00
    name="vnstock_financials_monthly",
)

vnstock_prices_schedule = ScheduleDefinition(
    job=vnstock_prices_job,
    cron_schedule="0 18 * * 1-5",        # weekdays 18:00 after HoSE close
    name="vnstock_prices_daily",
)

vnstock_ratios_schedule = ScheduleDefinition(
    job=vnstock_ratios_job,
    cron_schedule="30 18 * * 1-5",       # weekdays 18:30 after prices
    name="vnstock_ratios_daily",
)
