"""Unit tests for signals/breakout_portfolio.run_portfolio (synthetic prices, no DB)."""
from __future__ import annotations

import numpy as np

from signals.breakout_portfolio import run_portfolio

FEE, TAX = 0.0015, 0.001


def test_exit_price_is_the_positions_own_ticker():
    # ticker 0 flat, ticker 1 collapses later; both bought together, ticker 0 must exit flat (not at ticker 1's price)
    T = 40
    close = np.full((T, 2), 100.0)
    close[30:, 1] = 50.0
    volume = np.ones((T, 2))
    eq, trades = run_portfolio(close, volume, {5: [0, 1]}, FEE, TAX, 0)
    assert len(trades) == 2
    flat = (1 - FEE - TAX) / (1 + FEE) - 1
    assert any(abs(t - flat) < 1e-9 for t in trades)


def test_ceiling_locked_entry_is_skipped():
    close = np.full((30, 1), 100.0)
    close[6:, 0] = 107.0                                      # +7% on the entry session -> cannot buy
    eq, trades = run_portfolio(close, np.ones((30, 1)), {5: [0]}, FEE, TAX, 0)
    assert trades == [] and np.allclose(eq, 1.0)
