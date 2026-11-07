"""
portfolio/job_health.py — are the Dagster jobs the advisor depends on failing run after run?

freshness.py only sees the daily market tables. A job that fails without leaving a stale daily table (news indexing,
financial statements, flows) went unnoticed for 9 days (news_job, 02/11-07/11/2026: Ollama down → embeddings failed).
This reads the run history from the Dagster webserver and reports, per watched job, how many of its latest runs failed in a
row. vn-portfolio polls POST /advisor/job-health and alerts on Telegram (app/data_watch.py).

Only finished runs count (SUCCESS / FAILURE); queued, started and cancelled runs are skipped. Not watched on purpose:
financials_quarterly_job (fails every day on one illiquid UPCoM ticker, VNX — no effect on the rule engine).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

VN_TZ = timezone(timedelta(hours=7))
GRAPHQL_URL = os.getenv("DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/graphql")
RUN_LIMIT = 800                              # ≈ 10 days of runs of every job
FAIL_STREAK = 2                              # consecutive failed runs before a job counts as unhealthy

WATCHED: tuple[str, ...] = (
    "market_index_ingest_job", "ohlcv_ingest_job", "foreign_flows_job", "proprietary_flows_job",
    "vnstock_prices_job", "vnstock_financials_job", "corporate_events_job", "index_data_job",
    "news_job", "news_scored_job", "bctc_sync_job", "benchmark_ingest_job",
)

_QUERY = "{ runsOrError(limit: %d) { ... on Runs { results { jobName status startTime } } } }" % RUN_LIMIT


def evaluate(runs: list[dict], watched: tuple[str, ...] = WATCHED, streak: int = FAIL_STREAK) -> dict:
    """runs = [{jobName, status, startTime (epoch s)}] -> {healthy, jobs, problems}. Pure.
    jobs[name] = {failed_in_a_row, last_success (ISO or None), last_run (ISO or None)}; a job is a problem when its latest
    `streak` finished runs all failed. A watched job with no run in the history is not a problem (it may simply not run)."""
    by_job: dict[str, list[dict]] = {}
    for r in sorted((r for r in runs if r.get("status") in ("SUCCESS", "FAILURE") and r.get("startTime")),
                    key=lambda r: r["startTime"], reverse=True):
        by_job.setdefault(r["jobName"], []).append(r)
    jobs, problems = {}, []
    for name in watched:
        rs = by_job.get(name, [])
        n = next((i for i, r in enumerate(rs) if r["status"] == "SUCCESS"), len(rs))
        ok_run = next((r for r in rs if r["status"] == "SUCCESS"), None)
        jobs[name] = {"failed_in_a_row": n,
                      "last_success": _iso(ok_run["startTime"]) if ok_run else None,
                      "last_run": _iso(rs[0]["startTime"]) if rs else None}
        if n >= streak:
            since = f", lần thành công cuối {jobs[name]['last_success'][:16].replace('T', ' ')}" \
                if jobs[name]["last_success"] else ", chưa có lần thành công nào trong lịch sử"
            problems.append(f"{name} thất bại {n} lần liên tiếp{since}")
    return {"healthy": not problems, "jobs": jobs, "problems": problems}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, VN_TZ).isoformat(timespec="seconds")


def fetch_runs() -> list[dict]:
    r = httpx.post(GRAPHQL_URL, json={"query": _QUERY}, timeout=20)
    r.raise_for_status()
    data = (r.json().get("data") or {}).get("runsOrError") or {}
    if "results" not in data:
        raise RuntimeError(f"Dagster không trả danh sách run: {str(data)[:150]}")
    return data["results"]


def check(now: datetime | None = None) -> dict:
    """evaluate() on the live Dagster history, plus checked_at. Dagster unreachable → raises (the caller reports it)."""
    now = now or datetime.now(VN_TZ)
    return {**evaluate(fetch_runs()), "checked_at": now.isoformat(timespec="seconds")}
