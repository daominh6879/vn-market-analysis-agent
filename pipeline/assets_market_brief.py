"""
pipeline/assets_market_brief.py — Dagster assets for daily market brief pipeline.

Assets:
  foreign_flows_ingest    — fetch HOSE foreign buy/sell → foreign_flows (weekdays 17:30)
  corporate_events_ingest — scrape CafeF corporate events → corporate_events (weekdays 07:00)
  daily_brief             — generate market brief via LangGraph (weekdays 07:15)

Run order on a trading day:
  07:00  corporate_events_ingest   (same-day events still on CafeF)
  07:15  daily_brief               (uses yesterday's market data + today's events)
  17:30  foreign_flows_ingest      (after HOSE close 15:00, VCI data settled)
  18:00  market_index_daily_ingest (existing schedule)
  18:30  ohlcv_daily_ingest        (existing schedule)
"""


import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, Config, RetryPolicy, ScheduleDefinition, asset, define_asset_job


# ── Foreign flows ─────────────────────────────────────────────────────────────

class ForeignFlowsConfig(Config):
    market: str = "HOSE"
    date_override: str = ""   # YYYY-MM-DD; empty = today


@asset(
    group_name="market_brief",
    description=(
        "Fetch HOSE per-ticker foreign buy/sell from VCI price board → upsert foreign_flows. "
        "Run weekdays 17:30 after HoSE close (15:00). Idempotent (ON CONFLICT DO UPDATE)."
    ),
    retry_policy=RetryPolicy(max_retries=3, delay=120),
)
def foreign_flows_ingest(
    context: AssetExecutionContext,
    config: ForeignFlowsConfig,
) -> dict:
    from datetime import datetime
    from ingest.fetch_foreign_flows import fetch_live_today

    if config.date_override:
        target_date = datetime.strptime(config.date_override, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    context.log.info(f"foreign_flows_ingest: {target_date} / {config.market}")
    n = fetch_live_today(target_date)
    context.log.info(f"foreign_flows_ingest done — {n} rows upserted")
    return {"rows_upserted": n, "date": str(target_date), "market": config.market}


# ── Corporate events ──────────────────────────────────────────────────────────

class CorporateEventsConfig(Config):
    days_back: int = 1      # include events from N days ago (catches same-day events)
    days_ahead: int = 14    # look ahead window


@asset(
    group_name="market_brief",
    description=(
        "Scrape CafeF corporate events calendar → upsert corporate_events. "
        "Run weekdays 07:00 to capture same-day events before brief generation."
    ),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def corporate_events_ingest(
    context: AssetExecutionContext,
    config: CorporateEventsConfig,
) -> dict:
    from data.corporate_events_scraper import cleanup_old_events, scrape_events, upsert_events

    deleted = cleanup_old_events(days_keep=30)
    context.log.info(f"corporate_events_ingest: cleaned {deleted} old rows")

    start = date.today() - timedelta(days=config.days_back)
    end = date.today() + timedelta(days=config.days_ahead)
    context.log.info(f"corporate_events_ingest: scraping {start} → {end}")

    events = scrape_events(start, end)
    context.log.info(f"corporate_events_ingest: scraped {len(events)} events")

    n = upsert_events(events)
    context.log.info(f"corporate_events_ingest done — {n} rows upserted")
    return {"scraped": len(events), "upserted": n, "cleaned": deleted}


# ── Daily brief ───────────────────────────────────────────────────────────────

class DailyBriefConfig(Config):
    report_date: str = ""   # YYYY-MM-DD; empty = today
    out_dir: str = "info"   # directory for output file


@asset(
    group_name="market_brief",
    deps=[corporate_events_ingest],
    description=(
        "Generate daily market brief via LangGraph → write to info/DD_MM_YYYY.txt. "
        "Run weekdays 07:15 after corporate_events_ingest."
    ),
    retry_policy=RetryPolicy(max_retries=1, delay=30),
)
def daily_brief(
    context: AssetExecutionContext,
    config: DailyBriefConfig,
) -> dict:
    import time
    from agents.market_brief_graph import build_brief_graph, make_initial_state

    report_date = config.report_date.strip() or str(date.today())

    # Build output path: info/DD_MM_YYYY.txt
    from datetime import datetime
    dt = datetime.strptime(report_date, "%Y-%m-%d")
    out_file = f"{config.out_dir}/{dt.strftime('%d_%m_%Y')}.txt"

    context.log.info(f"daily_brief: date={report_date} out={out_file}")

    app = build_brief_graph()
    initial = make_initial_state(date=report_date, output_path=out_file)

    t0 = time.perf_counter()
    final = app.invoke(initial)
    elapsed = time.perf_counter() - t0

    missing = final.get("missing_fields", [])
    output_file = final.get("output_file", "")
    history = final.get("history", [])
    synth = next((h for h in history if h.get("step") == "compose_outlook"), {})

    context.log.info(
        f"daily_brief done — {elapsed:.1f}s, "
        f"tokens={synth.get('input_tokens',0)}+{synth.get('output_tokens',0)}, "
        f"missing={missing}"
    )

    if missing:
        context.log.warning(f"Missing fields: {missing}")

    return {
        "date": report_date,
        "output_file": output_file,
        "elapsed_s": round(elapsed, 1),
        "missing_fields": missing,
        "input_tokens": synth.get("input_tokens", 0),
        "output_tokens": synth.get("output_tokens", 0),
    }


# ── Jobs ──────────────────────────────────────────────────────────────────────

foreign_flows_job = define_asset_job(
    name="foreign_flows_job",
    selection=[foreign_flows_ingest],
)

corporate_events_job = define_asset_job(
    name="corporate_events_job",
    selection=[corporate_events_ingest],
)

daily_brief_job = define_asset_job(
    name="daily_brief_job",
    selection=[corporate_events_ingest, daily_brief],
)


# ── Schedules ─────────────────────────────────────────────────────────────────

foreign_flows_schedule = ScheduleDefinition(
    job=foreign_flows_job,
    cron_schedule="30 17 * * 1-5",   # weekdays 17:30 after HoSE close
    name="foreign_flows_1730",
)

corporate_events_schedule = ScheduleDefinition(
    job=corporate_events_job,
    cron_schedule="0 7 * * 1-5",     # weekdays 07:00
    name="corporate_events_0700",
)

daily_brief_schedule = ScheduleDefinition(
    job=daily_brief_job,
    cron_schedule="15 7 * * 1-5",    # weekdays 07:15 (after corporate_events at 07:00)
    name="daily_brief_0715",
)
