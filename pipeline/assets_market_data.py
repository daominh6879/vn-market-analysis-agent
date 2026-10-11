"""
pipeline/assets_market_data.py — Dagster assets for the "data gap" datasets, all persisted to Postgres.

Assets:
  securities_shares    — re-seed securities (sectors) + listed_shares   → securities        (Sun 02:00)
  stock_dividends      — dividend history (cash/stock), all tickers       → dividends         (weekdays 19:00)
  ratio_history        — per-year ROE/ROA/EPS/PE/PB/margins (all tickers) → financial_facts  (Sun 04:00)
  financials_quarterly — quarterly statements (period 'Qn/YYYY')        → financial_facts   (Sun 05:00)
  daily_snapshots      — SJC gold, VCB USD, SBV central rate            → daily_snapshots   (daily 09:00 + 18:00)

data_bootstrap_sensor fills any of these datasets that are empty or under-covered (fresh DB, partial load)
so nothing needs a manual first run. Run keys are per dataset per day: a failed bootstrap retries
the next day instead of looping.
"""

import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (
    AssetExecutionContext, Config, DefaultSensorStatus, DefaultScheduleStatus, RetryPolicy,
    RunRequest, ScheduleDefinition, SkipReason, asset, define_asset_job, sensor,
)

from pipeline.assets_vnstock import vnstock_financials_job, vnstock_ratios_job

_TZ = "Asia/Ho_Chi_Minh"
_VNSTOCK_PAUSE = 3.2   # stay under the vnstock rate limit
_SHARES_PAUSE = 0.0    # pacing lives in fetch_shares._paced_call (60 req/min limit)
_MAX_CONSECUTIVE_FAILURES = 15   # dead provider → stop instead of crawling through every ticker


def _tickers(scope: str) -> list[str]:
    from core.tickers import get_ratio_tickers, get_tickers
    return get_tickers() if scope == "all" else get_ratio_tickers()


def _fail_if_nothing(name: str, done: int, total: int, failed: list[str]) -> None:
    """A run that produced nothing must fail (so retries/alerts fire) instead of 'succeeding' empty."""
    if total and done == 0:
        raise RuntimeError(f"{name}: 0/{total} tickers succeeded; failed={failed[:10]}")


# ── securities: sectors + shares outstanding ─────────────────────────────────

@asset(
    group_name="market_data",
    description="Re-seed securities (sector overrides) and fill listed_shares (vnstock VCI, charter-capital fallback).",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def securities_shares(context: AssetExecutionContext) -> dict:
    from ingest.seed_securities import seed_from_json
    from ingest.fetch_shares import fetch_issue_share, fill_from_charter_capital, update_shares

    context.log.info(f"securities re-seeded: {seed_from_json()} rows")
    # Cheap DB-only fill first so market cap works even if the provider is down; vnstock values overwrite it.
    approx = fill_from_charter_capital()
    context.log.info(f"charter-capital fallback filled {approx} tickers")
    tickers = _tickers("all")
    ok, failed, streak = 0, [], 0
    for i, t in enumerate(tickers, 1):
        n = fetch_issue_share(t)
        if n:
            update_shares(t, n)
            ok += 1
            streak = 0
        else:
            failed.append(t)
            streak += 1
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                context.log.warning(f"{streak} consecutive failures at {t} — provider looks down, stopping early")
                break
        if i % 25 == 0:
            context.log.info(f"  progress {i}/{len(tickers)} ok={ok} failed={len(failed)}")
        time.sleep(_SHARES_PAUSE)
    context.log.info(f"listed_shares: vnstock {ok}/{len(tickers)}, charter-capital fallback {approx}")
    _fail_if_nothing("securities_shares", ok + approx, len(tickers), failed)
    return {"vnstock": ok, "charter_fallback": approx, "missing": len(failed)}


# ── dividends ────────────────────────────────────────────────────────────────

class DividendsConfig(Config):
    scope: str = "all"   # "all" = every active security, "ratio" = VN30+VN100 labels only


@asset(
    group_name="market_data",
    description="Dividend history (cash + stock) from vnstock events → Postgres dividends. Upserts.",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def stock_dividends(context: AssetExecutionContext, config: DividendsConfig) -> dict:
    from ingest.fetch_dividends import fetch_events_df, rows_from_events, upsert_dividends

    tickers = _tickers(config.scope)
    rows_total, done, failed = 0, 0, []
    for t in tickers:
        try:
            df = fetch_events_df(t)
            if df is not None and not df.empty:
                rows, skipped = rows_from_events(t, df)
                rows_total += upsert_dividends(rows)
                if skipped:
                    context.log.info(f"  {t}: {skipped} dividend events without a parsable figure")
            done += 1
        except (Exception, SystemExit) as exc:
            context.log.warning(f"  {t}: {exc}")
            failed.append(t)
        time.sleep(_VNSTOCK_PAUSE)
    context.log.info(f"dividends: {rows_total} rows from {done}/{len(tickers)} tickers")
    _fail_if_nothing("stock_dividends", done, len(tickers), failed)
    if tickers and rows_total == 0:   # tickers answered but nothing parsed → parser/schema drift, not success
        raise RuntimeError(f"stock_dividends: 0 rows from {done}/{len(tickers)} tickers (parser or provider schema changed?)")
    return {"rows": rows_total, "tickers": done, "failed": failed}


# ── per-year ratios ──────────────────────────────────────────────────────────

@asset(
    group_name="market_data",
    description="Per-fiscal-year ROE/ROA/EPS/PE/PB/margins (KBS) → financial_facts metric_code ratio_*.",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def ratio_history(context: AssetExecutionContext) -> dict:
    from ingest.fetch_ratios import fetch_and_upsert_history

    tickers = _tickers("all")
    facts, done, failed = 0, 0, []
    for t in tickers:
        try:
            n = fetch_and_upsert_history(t)
            if n:
                facts += n
                done += 1
            else:
                failed.append(t)
        except (Exception, SystemExit) as exc:
            context.log.warning(f"  {t}: {exc}")
            failed.append(t)
        time.sleep(_VNSTOCK_PAUSE)
    context.log.info(f"ratio_history: {facts} facts from {done}/{len(tickers)} tickers")
    _fail_if_nothing("ratio_history", done, len(tickers), failed)
    return {"facts": facts, "tickers": done, "failed": failed}


# ── quarterly statements ─────────────────────────────────────────────────────

class QuarterlyConfig(Config):
    years_back: int = 2   # current year and N previous


@asset(
    group_name="market_data",
    description="Quarterly income/balance/cash-flow (KBS) → financial_facts with period 'Qn/YYYY'. Upserts.",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def financials_quarterly(context: AssetExecutionContext, config: QuarterlyConfig) -> dict:
    from ingest.fetch_financials import fetch_finance_facts, insert_vnstock_facts

    tickers = _tickers("all")
    year = date.today().year
    facts, done, failed = 0, 0, []
    for t in tickers:
        try:
            batch = fetch_finance_facts(ticker=t, report_type="consolidated", period_from=year - config.years_back,
                                        period_to=year, source="KBS", quarterly=True)
            if batch:
                facts += insert_vnstock_facts(batch)
                done += 1
            else:
                failed.append(t)
        except (Exception, SystemExit) as exc:
            context.log.warning(f"  {t}: {exc}")
            failed.append(t)
        time.sleep(4.0)   # ~3 requests per ticker (IS, BS, CF)
    context.log.info(f"financials_quarterly: {facts} facts from {done}/{len(tickers)} tickers")
    _fail_if_nothing("financials_quarterly", done, len(tickers), failed)
    return {"facts": facts, "tickers": done, "failed": failed}


# ── daily snapshots ──────────────────────────────────────────────────────────

@asset(
    group_name="market_data",
    description="Snapshot SJC gold, VCB USD and SBV central rate → daily_snapshots (powers 'so với hôm qua').",
    retry_policy=RetryPolicy(max_retries=3, delay=120),
)
def daily_snapshots(context: AssetExecutionContext) -> dict:
    from ingest.fetch_daily_snapshots import run

    saved, missing = run()
    context.log.info(f"daily_snapshots saved={saved} unavailable={missing}")
    if not saved:
        raise RuntimeError(f"daily_snapshots: nothing saved; unavailable={missing}")
    return {"saved": saved, "unavailable": missing}


# ── Jobs ─────────────────────────────────────────────────────────────────────

securities_shares_job = define_asset_job("securities_shares_job", selection=[securities_shares])
stock_dividends_job = define_asset_job("stock_dividends_job", selection=[stock_dividends])
ratio_history_job = define_asset_job("ratio_history_job", selection=[ratio_history])
financials_quarterly_job = define_asset_job("financials_quarterly_job", selection=[financials_quarterly])
daily_snapshots_job = define_asset_job("daily_snapshots_job", selection=[daily_snapshots])


# ── Schedules ────────────────────────────────────────────────────────────────

def _schedule(job, cron: str, name: str, run_config: dict | None = None) -> ScheduleDefinition:
    return ScheduleDefinition(job=job, cron_schedule=cron, name=name, run_config=run_config,
                              default_status=DefaultScheduleStatus.RUNNING, execution_timezone=_TZ)


securities_shares_schedule = _schedule(securities_shares_job, "0 2 * * 0", "securities_shares_weekly")
stock_dividends_schedule = _schedule(stock_dividends_job, "0 19 * * 1-5", "stock_dividends_daily")
stock_dividends_all_schedule = _schedule(
    stock_dividends_job, "0 3 * * 0", "stock_dividends_all_weekly",
    run_config={"ops": {"stock_dividends": {"config": {"scope": "all"}}}},
)
ratio_history_schedule = _schedule(ratio_history_job, "0 4 * * 0", "ratio_history_weekly")
financials_quarterly_schedule = _schedule(financials_quarterly_job, "0 5 * * 0", "financials_quarterly_weekly")
daily_snapshots_schedule = _schedule(daily_snapshots_job, "0 9,18 * * *", "daily_snapshots_twice_daily")


# ── Bootstrap sensor: fill datasets that are still empty ─────────────────────

_COVERAGE_MIN = 0.8   # a dataset covering fewer than 80% of active securities is treated as incomplete
_ACTIVE = "(SELECT count(*) FROM securities WHERE is_active)"


def _tickers_below_coverage(table_filter: str) -> str:
    """SQL → true when the distinct active tickers matched by `table_filter` fall under _COVERAGE_MIN of all active."""
    return (f"SELECT (SELECT count(DISTINCT ticker) FROM {table_filter} "
            f"AND ticker IN (SELECT ticker FROM securities WHERE is_active)) < {_COVERAGE_MIN} * {_ACTIVE}")


_BOOTSTRAP_CHECKS: list[tuple[str, str, str]] = [
    # (job name, SQL returning true when the dataset is empty or under-covered, label)
    ("securities_shares_job",
     f"SELECT (SELECT count(*) FROM securities WHERE is_active AND listed_shares > 0) < {_COVERAGE_MIN} * {_ACTIVE}",
     "listed_shares"),
    ("stock_dividends_job",
     f"SELECT (SELECT count(DISTINCT ticker) FROM dividends) < 0.3 * {_ACTIVE}",   # not every ticker pays; 30% is a floor
     "dividends"),
    ("ratio_history_job", _tickers_below_coverage("financial_facts WHERE metric_code LIKE 'ratio\_%'"), "ratio_history"),
    ("financials_quarterly_job",
     _tickers_below_coverage("financial_facts WHERE period ~ '^Q[1-4]/[0-9]{4}$'"), "quarterly"),
    ("vnstock_financials_job",
     _tickers_below_coverage("financial_facts WHERE period ~ '^[0-9]{4}$' AND metric_code NOT LIKE 'ratio\_%'"),
     "annual_financials"),
    ("vnstock_ratios_job", _tickers_below_coverage("stock_ratios WHERE true"), "stock_ratios"),
    ("daily_snapshots_job", "SELECT NOT EXISTS (SELECT 1 FROM daily_snapshots)", "snapshots"),
]


@sensor(
    jobs=[securities_shares_job, stock_dividends_job, ratio_history_job, financials_quarterly_job, daily_snapshots_job,
          vnstock_financials_job, vnstock_ratios_job],
    name="data_bootstrap_sensor",
    minimum_interval_seconds=600,
    default_status=DefaultSensorStatus.RUNNING,
    description="Starts the job for any market-data table that is empty or covers <80% of active securities, once per day per dataset.",
)
def data_bootstrap_sensor(context):
    from core.db import get_conn

    today = date.today().isoformat()
    requests = []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for job_name, sql, label in _BOOTSTRAP_CHECKS:
                    cur.execute(sql)
                    if cur.fetchone()[0]:
                        requests.append(RunRequest(run_key=f"bootstrap-{label}-{today}", job_name=job_name,
                                                   tags={"bootstrap": label}))
    except (Exception, SystemExit) as exc:
        return SkipReason(f"bootstrap check failed: {exc}")
    return requests or SkipReason("all market-data datasets already populated")
