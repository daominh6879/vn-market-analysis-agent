"""
pipeline/assets_disclosures.py — datasets for vn-portfolio's rule engine news rules (khung v2.2, /advisor/news-facts).

Assets:
  news_scored            — LLM impact score for every unscored ticker-tagged article (every 6h at :40, after news_job)
  insider_trades_ingest  — VCI IQ insider / related-party / institution trade notices → insider_trades (weekdays 07:10)
  insider_deals_ingest   — CafeF GDCoDong deals with volumes (registered / executed, holding before / after)
                           → insider_deals (weekdays 07:30)

Bootstrap: data_bootstrap_sensor starts insider_trades_job with days_back 400 when the table is empty, and
news_scored_job with days_back 400 when fewer than half of last year's ticker-tagged articles are scored, and
insider_deals_job with days_back 400 when insider_deals is empty.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (AssetExecutionContext, Config, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset,
                     define_asset_job)


class NewsScoredConfig(Config):
    days_back: int = 3       # rescans this window; already-scored pairs are skipped
    workers: int = 4


@asset(
    group_name="news",
    description="LLM impact score (relevance, direction, magnitude) for every unscored ticker-tagged article → "
                "news_impact_scores (origin 'scheduled'). First judgement wins.",
    retry_policy=RetryPolicy(max_retries=2, delay=120),
)
def news_scored(context: AssetExecutionContext, config: NewsScoredConfig) -> dict:
    from portfolio.news_facts import recent_window, score_unscored

    since, until = recent_window(config.days_back)
    out = score_unscored(since, until, workers=config.workers)
    context.log.info(f"news_scored {since} → {until}: {out}")
    return out


class InsiderTradesConfig(Config):
    days_back: int = 10      # notices are updated with execution results; rescan a short window


@asset(
    group_name="market_brief",
    description="Insider / related-party / institution trade notices (VCI IQ DDIND, DDINS, DDRP) → insider_trades.",
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def insider_trades_ingest(context: AssetExecutionContext, config: InsiderTradesConfig) -> dict:
    from data.insider_trades import ingest

    out = ingest(config.days_back)
    context.log.info(f"insider_trades_ingest: {out}")
    if out["fetched"] == 0 and config.days_back >= 30:
        raise RuntimeError(f"insider_trades_ingest: 0 notices in {config.days_back} days (source empty or changed)")
    return out


class InsiderDealsConfig(Config):
    days_back: int = 30      # executed volumes are filled in after the plan window; rescan a month


@asset(
    group_name="market_brief",
    description="Insider / related-person / major-holder deals with volumes (CafeF GDCoDong) → insider_deals.",
    retry_policy=RetryPolicy(max_retries=2, delay=120),
)
def insider_deals_ingest(context: AssetExecutionContext, config: InsiderDealsConfig) -> dict:
    from core.tickers import get_tickers
    from data.cafef_insider import ingest

    tickers = get_tickers()
    out = ingest(tickers, config.days_back)
    context.log.info(f"insider_deals_ingest: {out}")
    if out["failed_count"] > len(tickers) / 2:
        raise RuntimeError(f"insider_deals_ingest: {out['failed_count']}/{len(tickers)} tickers failed {out['failed']}")
    return out


news_scored_job = define_asset_job(name="news_scored_job", selection=[news_scored])
insider_deals_job = define_asset_job(name="insider_deals_job", selection=[insider_deals_ingest])
insider_trades_job = define_asset_job(name="insider_trades_job", selection=[insider_trades_ingest])

news_scored_schedule = ScheduleDefinition(
    job=news_scored_job,
    cron_schedule="40 */6 * * *",          # news_6h runs at :00
    name="news_scored_6h",
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)
insider_deals_schedule = ScheduleDefinition(
    job=insider_deals_job,
    cron_schedule="30 7 * * 1-5",
    name="insider_deals_weekdays",
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)
insider_trades_schedule = ScheduleDefinition(
    job=insider_trades_job,
    cron_schedule="10 7 * * 1-5",
    name="insider_trades_weekdays",
    execution_timezone="Asia/Ho_Chi_Minh",
    default_status=DefaultScheduleStatus.RUNNING,
)
