"""Unit tests for portfolio/backtest_reversal.py (pre-registered VN30 reversal rule) and the benchmark bar parser.
Synthetic prices, no DB."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.assets_benchmark import bars
from portfolio.backtest_reversal import basket_net, evaluate, picks, summarise
from trend.features import Panel
from trend.labels import COST

TICKERS = [f"T{k:02d}" for k in range(8)]


def make_panel(n=60):
    days = pd.bdate_range("2026-09-01", periods=n)
    px = []
    for k, t in enumerate(TICKERS):           # T00 falls fastest, T07 rises fastest
        close = 20 * np.exp(np.arange(n) * 0.002 * (k - 3.5))
        px += [{"ticker": t, "date": d, "open": c * 0.99, "close": c, "volume": 1_000_000} for d, c in zip(days, close)]
    idx = pd.DataFrame({"date": days, "open": 1000.0, "close": 1000.0})
    flows = pd.DataFrame(columns=["ticker", "date", "net_value"])
    meta = pd.DataFrame({"ticker": TICKERS, "sector": "X", "company_name": TICKERS, "in_universe": True})
    return Panel(pd.DataFrame(px), idx, flows, meta), days


def test_losers_are_the_lowest_20_session_returns_among_liquid_names():
    p, _ = make_panel()
    got = picks(p, 30, k=3)
    assert got["losers"] == ["T00", "T01", "T02"] and got["winners"] == ["T07", "T06", "T05"]


def test_basket_buys_next_open_sells_close_after_costs():
    p, _ = make_panel()
    want = np.mean([p.close[t].iloc[35] / p.open[t].iloc[31] - 1 - COST for t in ("T00", "T01")])
    assert basket_net(p, ["T00", "T01"], 30, 5) == pytest.approx(want)


def test_excess_is_against_the_etf_over_the_same_window_and_open_windows_are_skipped():
    p, days = make_panel()
    etf = pd.DataFrame({"open": 30.0, "close": 30.3}, index=days)      # +1% gross every window
    df = evaluate(p, etf, days[30].date().isoformat())
    row = df[(df["session"] == days[30].date().isoformat()) & (df["horizon"] == 5)].iloc[0]
    assert row["etf_net"] == pytest.approx(0.01 - COST)
    assert row["losers_excess"] == pytest.approx(row["losers_net"] - row["etf_net"])
    assert df["session"].max() <= days[-6].date().isoformat()              # needs i + 5 < n
    s = {r["horizon"]: r for r in summarise(df)}
    assert s[5]["losers_excess"] < 0 < s[5]["winners_excess"]


def test_benchmark_bars_parse_provider_rows():
    df = pd.DataFrame([{"time": "2026-10-27", "open": 33.99, "high": 34.1, "low": 33.79, "close": 34.0, "volume": 142400}])
    assert bars(df) == [("2026-10-27", 33.99, 34.1, 33.79, 34.0, 142400)]
