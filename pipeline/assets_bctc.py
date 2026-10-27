"""
pipeline/assets_bctc.py — keep the latest annual BCTC of every active ticker in MinIO + Qdrant.

Assets:
  bctc_sync — CafeF latest annual report → MinIO `<ticker>-docs/<year>/<file>` → documents + Qdrant `bctc_structural`  (Sun 01:00)

Idempotent: only the missing MinIO object / Qdrant points are filled. data_bootstrap_sensor
(pipeline/assets_market_data.py) starts bctc_sync_job when too few tickers are indexed, so a fresh DB fills itself.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (
    AssetExecutionContext, Config, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset, define_asset_job,
)

_TZ = "Asia/Ho_Chi_Minh"


class BctcSyncConfig(Config):
    tickers: list[str] = []            # empty = all active tickers
    report_type: str = "standalone"    # "standalone" | "consolidated"


@asset(
    group_name="bctc",
    description="Ensure the latest annual BCTC (CafeF) of each active ticker exists in MinIO and is indexed in Qdrant bctc_structural.",
    retry_policy=RetryPolicy(max_retries=2, delay=600),
)
def bctc_sync(context: AssetExecutionContext, config: BctcSyncConfig) -> dict:
    from ingest.sync_bctc import run

    out = run(config.tickers or None, config.report_type)   # raises when no ticker succeeded
    statuses = list(out["results"].values())
    summary = {"synced": statuses.count("synced"), "up_to_date": statuses.count("up-to-date"),
               "failed": len(out["failed"]), "with_notes": len(out["notes"]),
               "balance_mismatch": sum("balance check mismatch" in n for n in out["notes"].values()),
               "balance_unverified": sum("balance check unverified" in n for n in out["notes"].values())}
    context.log.info(f"bctc_sync: {summary}; failed={list(out['failed'])[:20]}; notes={out['notes']}")
    return summary


bctc_sync_job = define_asset_job("bctc_sync_job", selection=[bctc_sync])

bctc_sync_schedule = ScheduleDefinition(
    job=bctc_sync_job, cron_schedule="0 1 * * 0", name="bctc_sync_weekly",
    default_status=DefaultScheduleStatus.RUNNING, execution_timezone=_TZ,
)
