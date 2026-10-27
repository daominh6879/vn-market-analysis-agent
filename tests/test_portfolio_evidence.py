"""Unit tests for portfolio/evidence.py: news grouping, candidate ranking, risk (no DB, no LLM)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from portfolio.evidence import (compute_risk, group_news, index_trend, price_reaction, rank_candidates, reprice,
                                ticker_stats, watch_candidates)

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


def test_price_reaction_from_the_close_before_the_article_day_to_the_latest():
    days = pd.to_datetime(["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"])
    close = pd.Series([100.0, 102.0, 105.0, 108.0, 110.0], index=days)
    index = pd.Series([1000.0, 1000.0, 1010.0, 1020.0, 1030.0], index=days)
    assert price_reaction(close, index, "2026-10-23") == {"ret_pct": 7.8, "vnindex_pct": 3.0, "sessions": 3}
    assert price_reaction(close, index, "2026-10-21") is None                     # no close before the article


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


def test_watch_candidates_skip_filters_but_not_held_or_unknown():
    u = universe(FALL=(0.02, -0.10, 1, 10_000, "Bank"), HELD=(0.02, 0.05, 50, 10_000, "Bank"))
    out = watch_candidates(u, {}, ["fall", "FALL", "HELD", "NOPE"], {"HELD"}, {"Bank": 0.2})
    assert [c["ticker"] for c in out] == ["FALL"]
    assert out[0]["watch"] is True and out[0]["foreign"] is None and out[0]["sector_nav_pct"] == 0.2
    assert watch_candidates(pd.DataFrame(), {}, ["FALL"], set(), {}) == []


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


def test_reprice_revalues_positions_weights_nav_and_return():
    ps = [{"ticker": "HPG", "quantity": 500, "avg_cost": 27_040.5, "last_price": 20_550.0, "market_value": 10_275_000.0,
           "unrealized_pct": -24.0, "weight": 0.65},
          {"ticker": "VCB", "quantity": 100, "avg_cost": 60_090.0, "last_price": 56_200.0, "market_value": 5_620_000.0,
           "unrealized_pct": -6.5, "weight": 0.35}]
    cash = {"cash": 80_915_500.0, "nav": 96_810_500.0, "total_deposited": 100_000_000.0, "total_withdrawn": 0.0,
            "market_value": 15_895_000.0, "total_return_pct": -3.19, "as_of": "2026-10-21"}
    out, c = reprice(ps, cash, {"HPG": 21_150.0})
    assert out[0]["last_price"] == 21_150.0 and out[0]["market_value"] == 10_575_000.0
    assert out[0]["unrealized_pct"] == pytest.approx((21_150 / 27_040.5 - 1) * 100)          # -21.8%, not -24%
    assert out[1]["last_price"] == 56_200.0                                                  # no quote: caller's price
    assert c["market_value"] == 16_195_000.0 and c["nav"] == 97_110_500.0
    assert c["total_return_pct"] == pytest.approx(-2.8895) and c["as_of"] == "2026-10-21"
    assert sum(p["weight"] for p in out) == pytest.approx(1.0) and ps[0]["last_price"] == 20_550.0   # input untouched


def test_ticker_stats_volatility_ma50_liquidity_and_short_history_left_out():
    dates = pd.date_range("2026-07-01", periods=61, freq="B")
    calm = [20.0 + (0.1 if i % 2 == 0 else 0.0) for i in range(61)]      # tiny swings, ends above its mean
    wild = [20.0 * (1.1 if i % 2 else 1.0) for i in range(60)] + [18.0]   # 10% swings, ends below MA50
    close = pd.DataFrame({"CALM": calm, "WILD": wild, "NEW": [None] * 40 + [10.0] * 21}, index=dates)
    value = pd.DataFrame({"CALM": [5.0] * 61, "WILD": [50.0] * 61, "NEW": [1.0] * 61}, index=dates)
    out = ticker_stats(close, value)
    assert set(out) == {"CALM", "WILD"}
    assert out["WILD"]["vol_60"] > 10 * out["CALM"]["vol_60"]
    assert out["CALM"]["price"] == pytest.approx(20_100) and out["CALM"]["above_ma50"] is True
    assert out["WILD"]["above_ma50"] is False and out["WILD"]["avg_value_bn"] == pytest.approx(50.0)


def test_index_trend_needs_50_sessions():
    assert index_trend(pd.Series([1000.0] * 49 + [900.0]))["above_ma50"] is False
    assert index_trend(pd.Series([1000.0] * 10))["ma50"] is None
