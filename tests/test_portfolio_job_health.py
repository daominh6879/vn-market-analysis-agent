"""Unit tests for portfolio/job_health.py (pure evaluate(), no Dagster, no DB)."""
from __future__ import annotations

from portfolio.job_health import evaluate

W = ("news_job", "ohlcv_ingest_job")


def run(job, status, t):
    return {"jobName": job, "status": status, "startTime": 1_780_000_000 + t}


def test_consecutive_failures_flag_the_job():
    r = evaluate([run("news_job", "SUCCESS", 0), run("news_job", "FAILURE", 10), run("news_job", "FAILURE", 20)], W)
    assert not r["healthy"] and r["jobs"]["news_job"]["failed_in_a_row"] == 2
    assert r["problems"] and "news_job thất bại 2 lần liên tiếp" in r["problems"][0]
    assert r["jobs"]["news_job"]["last_success"] is not None


def test_one_failure_or_a_later_success_is_healthy():
    assert evaluate([run("news_job", "SUCCESS", 0), run("news_job", "FAILURE", 10)], W)["healthy"]
    r = evaluate([run("news_job", "FAILURE", 0), run("news_job", "FAILURE", 10), run("news_job", "SUCCESS", 20)], W)
    assert r["healthy"] and r["jobs"]["news_job"]["failed_in_a_row"] == 0


def test_only_finished_runs_count_and_order_is_by_start_time_not_list_order():
    rs = [run("news_job", "FAILURE", 30), run("news_job", "STARTED", 40), run("news_job", "CANCELED", 25),
          run("news_job", "FAILURE", 20), run("news_job", "SUCCESS", 0)]
    assert evaluate(rs, W)["jobs"]["news_job"]["failed_in_a_row"] == 2
    assert evaluate(list(reversed(rs)), W)["jobs"]["news_job"]["failed_in_a_row"] == 2


def test_never_succeeded_and_unwatched_and_missing_jobs():
    r = evaluate([run("news_job", "FAILURE", 0), run("news_job", "FAILURE", 10), run("other_job", "FAILURE", 5),
                  run("other_job", "FAILURE", 6)], W)
    assert "chưa có lần thành công nào" in r["problems"][0]
    assert "other_job" not in r["jobs"]
    assert r["jobs"]["ohlcv_ingest_job"] == {"failed_in_a_row": 0, "last_success": None, "last_run": None}
