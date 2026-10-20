"""Unit tests for signals/breakout_monitor._dedupe (no DB)."""
from __future__ import annotations

import pandas as pd

from signals.breakout_monitor import _dedupe

CAL = pd.bdate_range("2026-01-05", periods=40)


def _sig(rows):
    return pd.DataFrame([(t, CAL[p], k, g) for t, p, k, g in rows], columns=["ticker", "date", "signal_type", "prop_gate"])


def test_types_on_nearby_days_merge_into_one_event_per_cooldown():
    sig = _sig([("AAA", 5, "SHORT", True), ("AAA", 8, "MID", True), ("AAA", 15, "LONG", False), ("BBB", 8, "MID", False)])
    kept = _dedupe(sig, CAL)
    assert sorted(zip(kept["ticker"], kept["pos"])) == [("AAA", 5), ("AAA", 15), ("BBB", 8)]   # 8 is inside AAA's cooldown, 15 is 10 sessions after 5
