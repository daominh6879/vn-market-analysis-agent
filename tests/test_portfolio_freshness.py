"""Unit tests for portfolio/freshness.py and the Dagster freshness re-run requests (no DB)."""
from __future__ import annotations

from datetime import date, datetime

from pipeline.assets_market_data import freshness_requests
from portfolio.freshness import VN_TZ, evaluate, expected_session


def vn(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=VN_TZ)


def test_expected_session_before_ready_hour_is_previous_trading_day():
    assert expected_session(vn(2026, 10, 21, 9, 20)) == date(2026, 10, 20)        # Wed intraday -> Tue
    assert expected_session(vn(2026, 10, 19, 9, 20)) == date(2026, 10, 16)        # Mon intraday -> Fri
    assert expected_session(vn(2026, 10, 21, 18, 59)) == date(2026, 10, 20)


def test_expected_session_after_ready_hour_is_today_and_skips_weekends_holidays():
    assert expected_session(vn(2026, 10, 21, 19, 0)) == date(2026, 10, 21)
    assert expected_session(vn(2026, 10, 24, 12)) == date(2026, 10, 23)           # Saturday -> Friday
    assert expected_session(vn(2026, 9, 3, 10)) == date(2026, 8, 31)              # 1-2/9 holidays
    assert expected_session(datetime(2026, 10, 21, 13, 0, tzinfo=None)) == date(2026, 10, 20)   # naive = VN time


def test_evaluate_flags_missing_session_and_partial_coverage():
    s = evaluate(date(2026, 10, 21), {
        "market_index": (date(2026, 10, 21), 1, 1),
        "ohlcv": (date(2026, 10, 21), 270, 391),
        "foreign_flows": (date(2026, 10, 19), 0, 431),
    })
    assert not s["fresh"] and s["expected_session"] == "2026-10-21"
    assert s["datasets"]["market_index"]["ok"] and not s["datasets"]["ohlcv"]["ok"]
    assert s["problems"] == ["ohlcv_daily phiên 2026-10-21 chỉ có 270/391 dòng (cần ≥ 90%)",
                             "foreign_flows mới đến phiên 2026-10-19, thiếu phiên 2026-10-21"]


def test_evaluate_fresh_when_every_dataset_covers_the_session():
    s = evaluate(date(2026, 10, 21), {"market_index": (date(2026, 10, 21), 1, 1), "ohlcv": (date(2026, 10, 21), 360, 391),
                                      "foreign_flows": (date(2026, 10, 21), 431, 431)})
    assert s["fresh"] and s["problems"] == []


def test_freshness_requests_backfill_gap_and_route_past_foreign_flows_to_ohlcv():
    s = evaluate(date(2026, 10, 20), {"market_index": (date(2026, 10, 16), 0, 1), "ohlcv": (date(2026, 10, 20), 200, 391),
                                      "foreign_flows": (date(2026, 10, 20), 100, 431)})
    reqs = {r.job_name: r for r in freshness_requests(s, date(2026, 10, 21), "2026102109")}
    assert set(reqs) == {"market_index_ingest_job", "ohlcv_ingest_job"}            # one ohlcv run covers both
    assert reqs["ohlcv_ingest_job"].run_config["ops"]["ohlcv_daily_ingest"]["config"]["days_back"] == 5
    assert reqs["market_index_ingest_job"].run_config["ops"]["market_index_daily_ingest"]["config"]["days"] == 30
    assert reqs["ohlcv_ingest_job"].run_key == "freshness-ohlcv_ingest_job-2026-10-20-2026102109"


def test_freshness_requests_today_foreign_flows_uses_live_board_job():
    s = evaluate(date(2026, 10, 21), {"market_index": (date(2026, 10, 21), 1, 1), "ohlcv": (date(2026, 10, 21), 391, 391),
                                      "foreign_flows": (date(2026, 10, 20), 0, 431)})
    assert [r.job_name for r in freshness_requests(s, date(2026, 10, 21), "h")] == ["foreign_flows_job"]
    assert freshness_requests(evaluate(date(2026, 10, 21), {}), date(2026, 10, 21), "h") == []
