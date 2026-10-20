"""Unit tests for portfolio/backtest_rules.simulate (synthetic paths, no DB)."""
from __future__ import annotations

import numpy as np

from portfolio.backtest_rules import simulate

FEE, TAX = 0.0015, 0.001


def path_of(closes):
    c = np.array(closes, dtype=float)
    return c / c[0], np.r_[0.0, c[1:] / c[:-1] - 1]


def test_hold_sells_at_horizon_net_of_costs():
    p, d = path_of([100] * 10)
    net, dd, t = simulate(p, d, None, None, FEE, TAX)
    assert t == 9 and dd == 0.0 and abs(net - ((1 - FEE - TAX) / (1 + FEE) - 1)) < 1e-12


def test_stop_fills_next_close_after_trigger():
    p, d = path_of([100, 100, 100, 90, 85, 80, 80])          # P/L <= -8% first at t=3 -> fill at t=4 (close 85)
    net, _, t = simulate(p, d, -8, None, FEE, TAX)
    assert t == 4 and abs(net - (0.85 * (1 - FEE - TAX) / (1 + FEE) - 1)) < 1e-12


def test_not_sellable_before_t2():
    p, d = path_of([100, 85, 100, 100, 100])                 # dip at t=1 is inside the T+2 window; recovered by t=2
    _, _, t = simulate(p, d, -8, None, FEE, TAX)
    assert t == 4


def test_floor_lock_delays_exit():
    p, d = path_of([100, 100, 100, 93, 86.5, 86, 86, 86])    # trigger t=3; t=4 is -7% floor-locked; fills at t=5
    _, _, t = simulate(p, d, -5, None, FEE, TAX)
    assert t == 5


def test_take_profit_exits_and_stop_never_fires_on_winner():
    p, d = path_of([100, 105, 112, 120, 130, 130])
    _, _, t_tp = simulate(p, d, None, 15, FEE, TAX)
    _, _, t_stop = simulate(p, d, -8, None, FEE, TAX)
    assert t_tp == 4 and t_stop == 5
