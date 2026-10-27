"""
pipeline/assets_trend.py — Ticker Trend Analyzer daily run (trend/).

trend_forecasts (weekdays 19:00, after OHLCV 18:30 and foreign flows 17:30):
  1. events: cluster + extract the universe's new articles (last 3 days) -> news_events;
  2. forecasts: Trend Judge (variant D) for every VN30 ticker on the latest session -> forecast_runs / daily_features;
  3. labels: realised outcomes for every forecast whose window has closed -> forecast_labels (calibration, track
     record).
Raises when no forecast could be made (stale prices, LLM down) so the run shows as failed.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, DefaultScheduleStatus, RetryPolicy, ScheduleDefinition, asset, define_asset_job


@asset(
    group_name="trend",
    description="Trend Judge forecasts (5 / 20 sessions, absolute + vs VNINDEX) for VN30 on the latest session, "
                "after syncing news events; labels closed windows. Weekdays 19:00.",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def trend_forecasts(context: AssetExecutionContext) -> dict:
    import warnings
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
    from trend.events import sync_events
    from trend.features import load_panel
    from trend.service import forecast, label_live_runs

    log = context.log.info
    panel = load_panel()
    session = panel.cal[-1].date()
    if (datetime.now().date() - session).days > 4:
        raise RuntimeError(f"latest price session {session} is stale")
    ev = sync_events(panel.universe, since=datetime.now(timezone.utc) - timedelta(days=3), log=log)
    items = forecast(panel.universe, log=log)
    ok = [i for i in items if "error" not in i and i.get("valid")]
    labelled = label_live_runs(log=log)
    if not ok:
        raise RuntimeError(f"no valid forecast for {session}: {[i.get('error') for i in items][:3]}")
    dist = {}
    for i in ok:
        for f in i["forecasts"]:
            k = f"{f['horizon']}d_{f['relative_trend_vs_vnindex']}"
            dist[k] = dist.get(k, 0) + 1
    log(f"trend: {len(ok)}/{len(items)} valid forecasts for {session}, relative trends {dist}")
    return {"session": session.isoformat(), "forecasts": len(ok), "invalid": len(items) - len(ok),
            "events_new": ev["extracted"], "labels": labelled, "relative_trends": dist}


trend_forecasts_job = define_asset_job(name="trend_forecasts_job", selection=[trend_forecasts])

trend_forecasts_schedule = ScheduleDefinition(
    job=trend_forecasts_job,
    cron_schedule="0 19 * * 1-5",    # weekdays 19:00, after OHLCV 18:30
    name="trend_forecasts_1900",
    default_status=DefaultScheduleStatus.RUNNING,
    execution_timezone="Asia/Ho_Chi_Minh",
)
