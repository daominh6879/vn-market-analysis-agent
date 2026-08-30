"""
pipeline/assets_vnstock.py — Dagster assets for vnstock data ingestion.

Assets:
  vnstock_financials — fetch annual financial statements → Postgres financial_facts
  vnstock_prices     — fetch daily stock prices → Postgres stock_prices

Schedules:
  vnstock_financials_schedule — 0 1 1 * *   (1st of month, 01:00)
  vnstock_prices_schedule     — 0 18 * * 1-5 (weekdays 18:00, after HoSE close)
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


# ── Jobs ───────────────────────────────────────────────────────────────────────

vnstock_financials_job = define_asset_job(
    name="vnstock_financials_job",
    selection=[vnstock_financials],
)

vnstock_prices_job = define_asset_job(
    name="vnstock_prices_job",
    selection=[vnstock_prices],
)


# ── Schedules ──────────────────────────────────────────────────────────────────

vnstock_financials_schedule = ScheduleDefinition(
    job=vnstock_financials_job,
    cron_schedule="0 1 1 * *",          # 1st of month 01:00
    name="vnstock_financials_monthly",
)

vnstock_prices_schedule = ScheduleDefinition(
    job=vnstock_prices_job,
    cron_schedule="0 18 * * 1-5",       # weekdays 18:00 after HoSE close
    name="vnstock_prices_daily",
)
