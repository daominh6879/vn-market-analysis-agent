"""
pipeline/assets_proprietary.py — proprietary (tự doanh) flows and the breakout forward-test log.

Assets:
  proprietary_flows_ingest — per-ticker tự doanh buy/sell (vnmarket)         → proprietary_flows   (weekdays 19:00)
  breakout_signals_daily   — latest session's breakout events + prop gate     → breakout_signals    (weekdays 19:30)

data_bootstrap_sensor (pipeline/assets_market_data.py) starts either job when its table is empty / under-covered, so a
fresh DB backfills proprietary flows from 2022-05-17 by itself.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (
    AssetCheckResult, AssetExecutionContext, Config, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset,
    asset_check, define_asset_job,
)

_TZ = "Asia/Ho_Chi_Minh"


class ProprietaryConfig(Config):
    backfill: bool = False      # True ignores stored dates and refetches from 2022-05-17 (bootstrap uses the empty-table path)


@asset(
    group_name="signals",
    description="Per-ticker proprietary (tự doanh) buy/sell from vnmarket → proprietary_flows (tỷ đồng). Incremental; fresh DB backfills from 2022-05-17.",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def proprietary_flows_ingest(context: AssetExecutionContext, config: ProprietaryConfig) -> dict:
    from ingest.fetch_proprietary_flows import run

    result = run(backfill=config.backfill)          # raises when fewer than half the tickers returned data
    context.log.info(f"proprietary_flows_ingest: {result['rows']} rows, {result['tickers_ok']} tickers, "
                     f"{result['no_data']} without data, {len(result['failed'])} failed")
    return {k: v for k, v in result.items() if k != "failed"} | {"failed": len(result["failed"])}


@asset(
    group_name="signals",
    deps=[proprietary_flows_ingest],
    description="Log the latest session's SHORT/MID/LONG breakout events with the proprietary-flow gate → breakout_signals (forward test).",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def breakout_signals_daily(context: AssetExecutionContext) -> dict:
    from signals.breakout_live import score_latest

    result = score_latest()                         # raises when proprietary_flows is stale
    context.log.info(f"breakout_signals_daily: {result}")
    return result


@asset_check(asset=breakout_signals_daily,
             description="Matured prop-gated breakout events (all types) must not do worse than ungated ones once there are enough of them.")
def gated_mid_beats_ungated() -> AssetCheckResult:
    from signals.breakout_monitor import MIN_EVENTS, realized

    r = realized()
    if r["gated_n"] < MIN_EVENTS or r["ungated_n"] < MIN_EVENTS:
        return AssetCheckResult(passed=True, description=f"fewer than {MIN_EVENTS} matured events per group yet", metadata=r)
    return AssetCheckResult(passed=r["gated_vs_universe"] >= r["ungated_vs_universe"], metadata=r)


proprietary_flows_job = define_asset_job("proprietary_flows_job", selection=[proprietary_flows_ingest])
breakout_signals_job = define_asset_job("breakout_signals_job", selection=[breakout_signals_daily])

proprietary_flows_schedule = ScheduleDefinition(
    job=proprietary_flows_job, cron_schedule="0 19 * * 1-5", name="proprietary_flows_1900",
    default_status=DefaultScheduleStatus.RUNNING, execution_timezone=_TZ,
)
breakout_signals_schedule = ScheduleDefinition(
    job=breakout_signals_job, cron_schedule="30 19 * * 1-5", name="breakout_signals_1930",
    default_status=DefaultScheduleStatus.RUNNING, execution_timezone=_TZ,
)
