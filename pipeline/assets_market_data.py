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
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import (
    AssetExecutionContext, Config, DagsterRunStatus, DefaultSensorStatus, DefaultScheduleStatus, RetryPolicy,
    RunRequest, RunsFilter, ScheduleDefinition, SkipReason, asset, define_asset_job, sensor,
)

from pipeline.assets_bctc import bctc_sync_job
from pipeline.assets_index import global_quotes_job, market_index_ingest_job
from pipeline.assets_market_brief import corporate_events_job, foreign_flows_job
from pipeline.assets_disclosures import insider_deals_job, insider_trades_job, news_scored_job
from pipeline.assets_ohlcv import ohlcv_ingest_job
from pipeline.assets_benchmark import benchmark_ingest_job
from pipeline.assets_proprietary import breakout_signals_job, proprietary_flows_job
from pipeline.assets_vnstock import vnstock_financials_job, vnstock_ratios_job
from pipeline.assets_rule_data import (
    bctc_notes_job, block_trades_job, commodities_job, corporate_actions_job, foreign_room_job, index_data_job,
    shareholders_job, vnd_items_job, fireant_data_job, price_history_job, yield_curve_job, macro_job,
    insurer_lines_job, intraday_job, broker_job,
)

_TZ = "Asia/Ho_Chi_Minh"
# Request pacing lives in vnmarket (per-source throttle), so the loops below do not sleep.
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
    description="Re-seed securities (sector overrides) and fill listed_shares (vnmarket, charter-capital fallback).",
    retry_policy=RetryPolicy(max_retries=2, delay=300),
)
def securities_shares(context: AssetExecutionContext) -> dict:
    from ingest.seed_securities import seed_from_json
    from ingest.fetch_shares import fetch_issue_share, fill_from_charter_capital, update_shares

    context.log.info(f"securities re-seeded: {seed_from_json()} rows")
    # Cheap DB-only fill first so market cap works even if the provider is down; vnmarket values overwrite it.
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
    context.log.info(f"listed_shares: vnmarket {ok}/{len(tickers)}, charter-capital fallback {approx}")
    _fail_if_nothing("securities_shares", ok + approx, len(tickers), failed)
    return {"vnmarket": ok, "charter_fallback": approx, "missing": len(failed)}


# ── dividends ────────────────────────────────────────────────────────────────

class DividendsConfig(Config):
    scope: str = "all"   # "all" = every active security, "ratio" = VN30+VN100 labels only


@asset(
    group_name="market_data",
    description="Dividend history (cash + stock) from vnmarket company.events → Postgres dividends. Upserts.",
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
    ("ratio_history_job", _tickers_below_coverage(r"financial_facts WHERE metric_code LIKE 'ratio\_%'"), "ratio_history"),
    ("financials_quarterly_job",
     _tickers_below_coverage("financial_facts WHERE period ~ '^Q[1-4]/[0-9]{4}$'"), "quarterly"),
    ("vnstock_financials_job",
     _tickers_below_coverage(r"financial_facts WHERE period ~ '^[0-9]{4}$' AND metric_code NOT LIKE 'ratio\_%'"),
     "annual_financials"),
    ("vnstock_ratios_job", _tickers_below_coverage("stock_ratios WHERE true"), "stock_ratios"),
    ("daily_snapshots_job", "SELECT NOT EXISTS (SELECT 1 FROM daily_snapshots)", "snapshots"),
    ("corporate_events_job", "SELECT NOT EXISTS (SELECT 1 FROM corporate_events)", "corporate_events"),
    ("proprietary_flows_job", _tickers_below_coverage("proprietary_flows WHERE true"), "proprietary_flows"),
    ("breakout_signals_job",
     "SELECT EXISTS (SELECT 1 FROM proprietary_flows) AND NOT EXISTS (SELECT 1 FROM breakout_signals)", "breakout_signals"),
    # depth, not freshness: a short run landing first (5-day default) leaves recent rows that data_freshness_sensor
    # counts as fresh, so the 1-year history the signals need would never be loaded
    ("ohlcv_ingest_job", "SELECT (SELECT count(DISTINCT date) FROM ohlcv_daily WHERE date >= CURRENT_DATE - 365) < 200",
     "ohlcv_history"),
    ("global_quotes_job", "SELECT NOT EXISTS (SELECT 1 FROM market_quotes)", "global_quotes"),
    # documents has no ticker column; not every ticker has an annual BCTC on CafeF, so 50% is a floor
    ("bctc_sync_job",
     f"SELECT (SELECT count(*) FROM documents WHERE status = 'active' AND collection = 'bctc_structural') < 0.5 * {_ACTIVE}",
     "bctc"),
    ("benchmark_ingest_job",
     "SELECT (SELECT count(*) FROM benchmark_daily WHERE date >= CURRENT_DATE - 365) < 200", "benchmark"),
    # vn-portfolio's rule engine news rules (khung v2.2): a year of history for its backtest
    ("insider_trades_job", "SELECT NOT EXISTS (SELECT 1 FROM insider_trades)", "insider_trades"),
    ("insider_deals_job", "SELECT NOT EXISTS (SELECT 1 FROM insider_deals)", "insider_deals"),
    ("news_scored_job",
     "SELECT (SELECT count(*) FROM news_impact_scores s JOIN news_articles a ON a.id = s.article_id "
     "WHERE s.kind = 'ticker' AND a.published_at >= CURRENT_DATE - 365) < 0.5 * (SELECT count(*) "
     "FROM news_articles a, unnest(a.tickers) t WHERE a.published_at >= CURRENT_DATE - 365)", "news_scores"),
    # vn-portfolio rule engine datasets (pipeline/assets_rule_data.py): full history on an empty / under-covered table
    ("block_trades_job", _tickers_below_coverage("block_trades WHERE true"), "block_trades"),
    ("index_data_job", "SELECT NOT EXISTS (SELECT 1 FROM index_valuation) OR NOT EXISTS "
     "(SELECT 1 FROM market_index_daily WHERE index_code = 'VNMID')", "index_data"),
    ("foreign_room_job", _tickers_below_coverage("foreign_room WHERE true"), "foreign_room"),
    ("corporate_actions_job", "SELECT NOT EXISTS (SELECT 1 FROM corporate_actions)", "corporate_actions"),
    ("commodities_job", "SELECT NOT EXISTS (SELECT 1 FROM commodity_daily)", "commodities"),
    ("shareholders_job", "SELECT NOT EXISTS (SELECT 1 FROM shareholders)", "shareholders"),
    ("vnd_items_job", "SELECT NOT EXISTS (SELECT 1 FROM vnd_financial_items WHERE model_type = 33)", "vnd_items"),
    ("bctc_notes_job", "SELECT NOT EXISTS (SELECT 1 FROM bctc_note_facts)", "bctc_notes"),
    ("fireant_data_job", "SELECT NOT EXISTS (SELECT 1 FROM fireant_financial_data)", "fireant_data"),
    ("yield_curve_job", "SELECT NOT EXISTS (SELECT 1 FROM gov_bond_yields)", "yield_curve"),
    ("macro_job", "SELECT NOT EXISTS (SELECT 1 FROM macro_indicators WHERE source <> 'vcb')", "macro"),
    ("insurer_lines_job", "SELECT NOT EXISTS (SELECT 1 FROM fireant_statement_lines)", "insurer_lines"),
    ("intraday_job", "SELECT NOT EXISTS (SELECT 1 FROM intraday_snapshot)", "intraday"),
    ("broker_job", "SELECT NOT EXISTS (SELECT 1 FROM broker_reports)", "broker"),
    ("price_history_job", "SELECT (SELECT count(DISTINCT ticker) FROM ohlcv_daily WHERE date < '2018-01-01') < 0.5 * "
     f"{_ACTIVE}", "price_history"),
]
_BOOTSTRAP_RUN_CONFIG: dict[str, dict] = {
    "ohlcv_history": {"ops": {"ohlcv_daily_ingest": {"config": {"days_back": 365, "backfill": True}}}},
    "insider_trades": {"ops": {"insider_trades_ingest": {"config": {"days_back": 400}}}},
    "insider_deals": {"ops": {"insider_deals_ingest": {"config": {"days_back": 400}}}},
    "news_scores": {"ops": {"news_scored": {"config": {"days_back": 400}}}},
    "block_trades": {"ops": {"block_trades_ingest": {"config": {"backfill": True}}}},
    "index_data": {"ops": {"size_indices_ingest": {"config": {"backfill": True}},
                           "index_valuation_ingest": {"config": {"backfill": True}}}},
    "foreign_room": {"ops": {"foreign_room_ingest": {"config": {"backfill": True}}}},
    "corporate_actions": {"ops": {"corporate_actions_ingest": {"config": {"backfill": True}}}},
    "commodities": {"ops": {"commodities_ingest": {"config": {"backfill": True}}}},
    "yield_curve": {"ops": {"yield_curve_ingest": {"config": {"backfill": True}}}},
    "macro": {"ops": {"macro_ingest": {"config": {"backfill": True}}}},
    "insurer_lines": {"ops": {"insurer_lines_ingest": {"config": {"backfill": True}}}},
    "intraday": {"ops": {"intraday_ingest": {"config": {"backfill": True}}}},
    "broker": {"ops": {"broker_reports_ingest": {"config": {"backfill": True}}}},
}


@sensor(
    jobs=[securities_shares_job, stock_dividends_job, ratio_history_job, financials_quarterly_job, daily_snapshots_job,
          vnstock_financials_job, vnstock_ratios_job, corporate_events_job,
          proprietary_flows_job, breakout_signals_job, ohlcv_ingest_job, global_quotes_job, bctc_sync_job,
          benchmark_ingest_job, insider_trades_job, news_scored_job, insider_deals_job,
          block_trades_job, index_data_job, foreign_room_job, corporate_actions_job, commodities_job, shareholders_job,
          vnd_items_job, bctc_notes_job, fireant_data_job, price_history_job, yield_curve_job, macro_job,
          insurer_lines_job, intraday_job, broker_job],
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
                                                   run_config=_BOOTSTRAP_RUN_CONFIG.get(label, {}),
                                                   tags={"bootstrap": label}))
    except (Exception, SystemExit) as exc:
        return SkipReason(f"bootstrap check failed: {exc}")
    return requests or SkipReason("all market-data datasets already populated")


# ── Freshness sensor: re-run the ingest of a daily dataset that misses the last finished session ──
# Covers failed, cancelled or partial runs and missed schedules while the daemon was down (portfolio/freshness.py
# decides what is stale). One request per job per hour while stale; vn-portfolio alerts if Dagster itself is down.

_IN_PROGRESS = [DagsterRunStatus.QUEUED, DagsterRunStatus.NOT_STARTED, DagsterRunStatus.STARTING, DagsterRunStatus.STARTED]
_STUCK_AFTER_S = 3600            # an "in progress" run older than this belongs to a killed process; ignore it


def _freshness_run_config(job: str, latest: str | None, expected: date) -> dict:
    gap = (expected - date.fromisoformat(latest)).days if latest else 365
    if job == "ohlcv_ingest_job":
        return {"ops": {"ohlcv_daily_ingest": {"config": {"days_back": min(max(gap + 3, 5), 365)}}}}
    if job == "market_index_ingest_job":
        return {"ops": {"market_index_daily_ingest": {"config": {"days": max(gap + 5, 30)}}}}
    return {}


def freshness_requests(status: dict, today: date, slot: str) -> list[RunRequest]:
    """Run requests for the stale datasets of a portfolio.freshness status. `slot` (e.g. the hour) goes into the run key
    so a dataset that stays stale is retried once per slot. Pure."""
    expected = date.fromisoformat(status["expected_session"])
    configs: dict[str, dict] = {}
    for label, d in status["datasets"].items():
        if d["ok"]:
            continue
        # foreign_flows_job reads the live price board (today only); past sessions come with the OHLCV ingest (Fireant)
        job = "ohlcv_ingest_job" if label == "foreign_flows" and expected < today else d["job"]
        configs.setdefault(job, _freshness_run_config(job, d["latest"], expected))
    return [RunRequest(run_key=f"freshness-{job}-{expected}-{slot}", job_name=job, run_config=cfg,
                       tags={"freshness": expected.isoformat()}) for job, cfg in configs.items()]


def _in_progress(instance, job: str) -> bool:
    recs = instance.get_run_records(filters=RunsFilter(job_name=job, statuses=_IN_PROGRESS), limit=5)
    return any(time.time() - r.create_timestamp.timestamp() < _STUCK_AFTER_S for r in recs)


@sensor(
    jobs=[ohlcv_ingest_job, market_index_ingest_job, foreign_flows_job],
    name="data_freshness_sensor",
    minimum_interval_seconds=900,
    default_status=DefaultSensorStatus.RUNNING,
    description="Re-runs the ingest of ohlcv_daily / market_index_daily / foreign_flows when they miss or only partly cover "
                "the last finished session (hourly while stale).",
)
def data_freshness_sensor(context):
    from portfolio.freshness import VN_TZ, check

    try:
        status = check()
    except (Exception, SystemExit) as exc:
        return SkipReason(f"freshness check failed: {exc}")
    if status["fresh"]:
        return SkipReason(f"daily data complete for {status['expected_session']}")
    now = datetime.now(VN_TZ)
    requests = [r for r in freshness_requests(status, now.date(), now.strftime("%Y%m%d%H"))
                if not _in_progress(context.instance, r.job_name)]
    context.log.warning("stale: " + "; ".join(status["problems"]))
    return requests or SkipReason("stale, re-run already in progress: " + "; ".join(status["problems"]))
