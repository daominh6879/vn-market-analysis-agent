"""
pipeline/assets_ohlcv.py — Dagster asset: fetch VN30 + index OHLCV → ohlcv_daily.

Schedule: weekdays 18:30 (after HoSE close 15:00, VCI data settled).
Tickers: VN30 constituents + VN30 index proxy.
Days back: 5 (incremental daily), configurable up to 365 for backfill.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, Config, RetryPolicy, ScheduleDefinition, asset, define_asset_job


# VN30 constituents + VN30 index itself
_OHLCV_TICKERS: list[str] = [
    "VN30",
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PDR", "PLX", "POW", "SAB", "SHB", "SSB", "SSI",
    "STB", "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB",
]

# Additional tickers from TICKERS env (extra stocks user wants tracked)
_extra = [t.strip().upper() for t in os.getenv("TICKERS", "").split(",") if t.strip()]
_OHLCV_TICKERS = list(dict.fromkeys(_OHLCV_TICKERS + _extra))  # dedup, preserve order


class OhlcvIngestConfig(Config):
    days_back: int = 5      # incremental default; use 365 for full historical backfill
    tickers: str = ""       # override list (comma-separated); empty = use _OHLCV_TICKERS


@asset(
    group_name="ohlcv",
    description=(
        "Fetch daily OHLCV for VN30 + constituents from VCI → upsert ohlcv_daily. "
        "Idempotent (ON CONFLICT DO UPDATE). Run after HoSE close (18:30 weekdays)."
    ),
    retry_policy=RetryPolicy(max_retries=3, delay=60),
)
def ohlcv_daily_ingest(context: AssetExecutionContext, config: OhlcvIngestConfig) -> dict:
    from ingest.fetch_ohlcv import fetch_and_upsert

    ticker_list = (
        [t.strip().upper() for t in config.tickers.split(",") if t.strip()]
        if config.tickers
        else _OHLCV_TICKERS
    )

    total_rows = 0
    failed: list[str] = []

    for ticker in ticker_list:
        context.log.info(f"Fetching OHLCV: {ticker} ({config.days_back}d)")
        try:
            n = fetch_and_upsert(ticker, config.days_back)
            total_rows += n
            context.log.info(f"  {ticker}: {n} rows upserted")
        except Exception as exc:
            context.log.error(f"  {ticker} FAILED: {exc}")
            failed.append(ticker)

    if failed:
        context.log.warning(f"Failed tickers: {failed}")

    context.log.info(f"ohlcv_daily_ingest done: {total_rows} rows, {len(failed)} failed")
    return {"total_rows": total_rows, "tickers": ticker_list, "failed": failed}


ohlcv_ingest_job = define_asset_job(
    name="ohlcv_ingest_job",
    selection=[ohlcv_daily_ingest],
)

ohlcv_ingest_schedule = ScheduleDefinition(
    job=ohlcv_ingest_job,
    cron_schedule="30 18 * * 1-5",   # weekdays 18:30 after HoSE close
    name="ohlcv_daily_1830",
)
