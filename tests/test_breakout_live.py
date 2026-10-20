"""Unit tests for signals/breakout_live.build_rows gating (synthetic panel, patched signals/flows)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals import breakout_live as bl


def test_build_rows_flags_prop_gate_and_skips_illiquid(monkeypatch):
    dates = pd.bdate_range("2026-01-01", periods=30)
    cols = ["AAA", "BBB", "CCC"]
    close = pd.DataFrame(20.0, index=dates, columns=cols)
    volume = pd.DataFrame(1_000_000.0, index=dates, columns=cols)       # 20 tỷ/day -> liquid
    volume["CCC"] = 1_000.0                                              # 0.02 tỷ/day -> illiquid
    wide = {"close": close, "volume": volume}
    hit = pd.DataFrame(False, index=dates, columns=cols)
    hit.iloc[-1] = True                                                  # every ticker has a MID signal today
    sg = {k: pd.DataFrame(False, index=dates, columns=cols) for k in bl.TYPES}
    sg["MID"] = hit
    monkeypatch.setattr(bl, "signals", lambda w, i, v: sg)
    ratios = {5: pd.DataFrame([[0.02, -0.03, 0.05]] * 30, index=dates, columns=cols),
              20: pd.DataFrame([[0.01, np.nan, 0.0]] * 30, index=dates, columns=cols)}
    monkeypatch.setattr(bl, "prop_ratio", lambda w, window: ratios[window])
    rows = bl.build_rows(wide, pd.Series(1000.0, index=dates))
    by_ticker = {r[0]: r for r in rows}
    assert set(by_ticker) == {"AAA", "BBB"}                              # CCC filtered by liquidity
    assert by_ticker["AAA"][7] is True and by_ticker["BBB"][7] is False  # gate = 5d ratio > 1%
    assert by_ticker["BBB"][6] is None                                   # NaN 20d ratio stored as NULL
