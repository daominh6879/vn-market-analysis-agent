"""
portfolio/freshness.py — is the daily market data the advisor reads complete for the last finished session?

Dagster fills the daily tables after the close (foreign flows 17:30, index 18:00, OHLCV 18:30). Until READY_HOUR the last
finished session counted as "expected" is the previous trading day. A dataset is fresh when it holds rows for the expected
session covering at least COVERAGE_MIN of its usual daily row count (max over the BASELINE_SESSIONS sessions before).

Used by the advise gate (stale -> StaleDataError, no advice on old data), POST /advisor/freshness (vn-portfolio alerts
when Dagster is down) and pipeline data_freshness_sensor (re-runs the job of a stale dataset).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tools.vn_holidays import is_vn_holiday

VN_TZ = timezone(timedelta(hours=7))       # no DST in Vietnam
READY_HOUR = 19                            # daily ingests (17:30-18:40) are done by then
COVERAGE_MIN = 0.9
BASELINE_SESSIONS = 10

# (label, table, row filter, Dagster job that fills it)
DATASETS: tuple[tuple[str, str, str, str], ...] = (
    ("market_index", "market_index_daily", "index_code = 'VNINDEX'", "market_index_ingest_job"),
    ("ohlcv", "ohlcv_daily", "TRUE", "ohlcv_ingest_job"),
    ("foreign_flows", "foreign_flows", "TRUE", "foreign_flows_job"),
)


class StaleDataError(RuntimeError):
    """The advisor refuses to judge on market data that misses the last finished session."""

    def __init__(self, status: dict):
        super().__init__("dữ liệu thị trường chưa cập nhật: " + "; ".join(status["problems"]))
        self.status = status


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and not is_vn_holiday(d.isoformat())


def expected_session(now: datetime | None = None) -> date:
    """Last session whose daily data should already be in the DB at `now` (VN time)."""
    now = now.astimezone(VN_TZ) if now and now.tzinfo else now or datetime.now(VN_TZ)
    d = now.date()
    if is_trading_day(d) and now.hour >= READY_HOUR:
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def evaluate(expected: date, stats: dict[str, tuple[date | None, int, int]]) -> dict:
    """stats = {label: (latest date, rows on expected session, baseline rows per session)} -> status. Pure."""
    jobs = {label: job for label, _, _, job in DATASETS}
    tables = {label: table for label, table, _, _ in DATASETS}
    datasets, problems = {}, []
    for label, (latest, rows, baseline) in stats.items():
        ok = latest is not None and latest >= expected and rows >= COVERAGE_MIN * max(baseline, 1)
        datasets[label] = {"latest": latest.isoformat() if latest else None, "rows": rows, "baseline": baseline,
                           "ok": ok, "job": jobs.get(label)}
        if ok:
            continue
        if latest is None or latest < expected:
            problems.append(f"{tables.get(label, label)} mới đến phiên {latest or 'chưa có'}, thiếu phiên {expected}")
        else:
            problems.append(f"{tables.get(label, label)} phiên {expected} chỉ có {rows}/{baseline} dòng "
                            f"(cần ≥ {COVERAGE_MIN:.0%})")
    return {"expected_session": expected.isoformat(), "fresh": not problems, "datasets": datasets, "problems": problems}


def check(now: datetime | None = None) -> dict:
    """evaluate() on the live DB, plus checked_at."""
    from core.db import get_conn

    now = now or datetime.now(VN_TZ)
    expected = expected_session(now)
    stats = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for label, table, where, _ in DATASETS:
                cur.execute(
                    f"SELECT (SELECT MAX(date) FROM {table} WHERE {where}), "
                    f"(SELECT count(*) FROM {table} WHERE {where} AND date = %(d)s), "
                    f"(SELECT COALESCE(MAX(n), 0) FROM (SELECT count(*) n FROM {table} WHERE {where} AND date < %(d)s "
                    f" GROUP BY date ORDER BY date DESC LIMIT %(k)s) b)",
                    {"d": expected, "k": BASELINE_SESSIONS})
                latest, rows, baseline = cur.fetchone()
                stats[label] = (latest, int(rows), int(baseline))
    return {**evaluate(expected, stats), "checked_at": now.isoformat(timespec="seconds")}


def require_fresh() -> dict:
    """check(), raising StaleDataError when any dataset is stale."""
    status = check()
    if not status["fresh"]:
        raise StaleDataError(status)
    return status
