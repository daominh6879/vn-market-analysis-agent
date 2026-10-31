"""pipeline/assets_market_data.financial_gap_requests: one request per job per day, only the tickers with gaps (no DB)."""
from __future__ import annotations

from datetime import date

from pipeline import assets_market_data as A


def test_requests_target_only_gap_tickers():
    reqs = A.financial_gap_requests(["ANN", "BBB"], ["STL", "BBB"], date(2026, 10, 31))
    by = {r.job_name: r for r in reqs}
    assert set(by) == {"vnstock_financials_job", "financials_quarterly_job", "fireant_financials_job"}
    cfg = by["vnstock_financials_job"].run_config["ops"]["vnstock_financials"]["config"]
    assert cfg == {"tickers": "ANN,BBB", "period_from": 2018, "period_to": 2026}
    assert by["financials_quarterly_job"].run_config["ops"]["financials_quarterly"]["config"]["tickers"] == "STL,BBB"
    assert by["fireant_financials_job"].run_config["ops"]["fireant_data_ingest"]["config"]["tickers"] == "ANN,BBB,STL"
    assert by["vnstock_financials_job"].run_key == "fin-gap-annual-2026-10-31"     # once per day


def test_no_gaps_no_requests_and_cap():
    assert A.financial_gap_requests([], [], date(2026, 10, 31)) == []
    many = [f"T{i:03d}" for i in range(A.GAP_MAX_TICKERS + 50)]
    r = A.financial_gap_requests(many, [], date(2026, 10, 31))
    assert len(r[0].run_config["ops"]["vnstock_financials"]["config"]["tickers"].split(",")) == A.GAP_MAX_TICKERS
