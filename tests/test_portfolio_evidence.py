"""Unit tests for portfolio/evidence.py: news grouping, candidate ranking, risk (no DB, no LLM)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from portfolio.evidence import compute_risk, group_news, rank_candidates

D = datetime(2026, 10, 18, 9, 0)


def row(id_, tickers, title="t", body="b " * 500):
    return (id_, f"http://n/{id_}", title, body, "cafef", D, tickers)


def test_group_news_every_ticker_has_a_key_even_without_news():
    out = group_news([row(1, ["AAA"])], ["AAA", "BBB"], per_ticker=8)
    assert [a["id"] for a in out["AAA"]] == [1] and out["BBB"] == []


def test_group_news_caps_per_ticker_truncates_snippet_and_ignores_other_tickers():
    rows = [row(i, ["AAA", "ZZZ"]) for i in range(5)]
    out = group_news(rows, ["AAA"], per_ticker=3)
    assert [a["id"] for a in out["AAA"]] == [0, 1, 2] and "ZZZ" not in out
    assert len(out["AAA"][0]["snippet"]) <= 400 and out["AAA"][0]["published_at"] == "2026-10-18"


def universe(**rows):
    return pd.DataFrame.from_dict(rows, orient="index", columns=["vol_20", "ret_20", "avg_value_bn", "price", "sector"])


def flow(n5, n20=1.0):
    return {"net_5d_bn": n5, "net_20d_bn": n20}


def test_rank_candidates_filters():
    u = universe(HELD=(0.02, 0.05, 50, 10_000, "Bank"), ILLQ=(0.02, 0.05, 1, 10_000, "Bank"),
                 FALL=(0.02, -0.05, 50, 10_000, "Bank"), HOT=(0.02, 0.40, 50, 10_000, "Bank"),
                 NOFLOW=(0.02, 0.05, 50, 10_000, "Bank"), SELLING=(0.02, 0.05, 50, 10_000, "Bank"),
                 OK=(0.02, 0.05, 50, 10_000, "Bank"))
    flows = {"HELD": flow(5), "ILLQ": flow(5), "FALL": flow(5), "HOT": flow(5), "SELLING": flow(-1), "OK": flow(5)}
    assert [c["ticker"] for c in rank_candidates(u, flows, {"HELD"}, {})] == ["OK"]


def test_rank_candidates_prefers_sectors_under_cap_then_foreign_flow_and_limits_count():
    rows = {f"T{i}": (0.02, 0.05, 50, 10_000, "Steel" if i == 0 else "Tech") for i in range(8)}
    flows = {t: flow(1, 10 - i) for i, t in enumerate(rows)}
    out = rank_candidates(universe(**rows), flows, set(), {"Steel": 0.5})
    assert len(out) == 5 and out[0]["ticker"] == "T1" and "T0" not in [c["ticker"] for c in out]


def test_rank_candidates_empty_universe():
    assert rank_candidates(pd.DataFrame(), {}, set(), {}) == []


def _pos(ticker, qty, price=20_000.0, sector="Bank"):
    return {"ticker": ticker, "market_value": qty * price, "sector": sector}


def test_risk_flags_and_beta():
    ps = [_pos("AAA", 15_000), _pos("BBB", 5_000, sector="Steel")]
    risk = compute_risk(ps, {"cash": 20_000_000.0, "nav": 420_000_000.0}, {"AAA": 1.7, "BBB": 0.5})
    assert risk["largest_ticker"] == "AAA" and risk["largest_ticker_pct"] == pytest.approx(300 / 420 * 100)
    assert {"ticker_concentration", "sector_concentration", "cash_low", "beta_high"} <= set(risk["flags"])
    assert risk["beta"] == pytest.approx((300 * 1.7 + 100 * 0.5) / 400)


def test_risk_cash_high_and_no_beta():
    risk = compute_risk([_pos("AAA", 100)], {"cash": 900_000_000.0, "nav": 902_000_000.0}, {})
    assert "cash_high" in risk["flags"] and risk["beta"] is None


def test_high_vol_flag_only_for_top_decile_holdings():
    cash = {"cash": 500_000_000.0, "nav": 1_000_000_000.0}
    vols = {"AAA": {"vol_20": 0.03, "vol_pct": 0.95}, "BBB": {"vol_20": 0.01, "vol_pct": 0.30}}
    r = compute_risk([_pos("AAA", 1000), _pos("BBB", 1000)], cash, {}, vols)
    assert r["flags"].count("vol_high") == 1 and [h["ticker"] for h in r["high_vol"]] == ["AAA"]
    assert compute_risk([_pos("AAA", 1000)], cash, {})["high_vol"] == []
