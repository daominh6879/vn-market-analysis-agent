"""
trend/labels.py — what actually happened after a signal (plan §7.1).

A signal on session D's close can only trade at D+1: buy at D+1's open, sell at the close h sessions after D, minus
fees both sides and the sell tax. alpha = that net return - VNINDEX over the same open -> close.
Labels use thresholds fixed BEFORE any backtest (27/10/2026, from the VN30 forward-return terciles of 10/2025-10/2026,
rounded; no model output had been seen): bullish above +THRESHOLD, bearish below -THRESHOLD, neutral in between.
A signal whose D+1 open sits at the ceiling (>= +6.5% over D's close) cannot be bought: fillable = False.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_VERSION = "l1"
HORIZONS = (5, 20)
COST = 0.0015 * 2 + 0.001        # broker fee both sides + 0.1% sell tax
ABS_THRESHOLD = {5: 0.02, 20: 0.04}
REL_THRESHOLD = {5: 0.02, 20: 0.03}
CEILING = 0.065


def classify(x: float, threshold: float) -> str:
    return "bullish" if x > threshold else "bearish" if x < -threshold else "neutral"


def label(panel, ticker: str, i: int, h: int) -> dict | None:
    """Label of a signal on calendar index i, or None when the window is not complete yet."""
    if i + h >= len(panel.cal) or ticker not in panel.close.columns:
        return None
    entry, exit_ = panel.open[ticker].iloc[i + 1], panel.close[ticker].iloc[i + h]
    i_entry, i_exit = panel.index_open.iloc[i + 1], panel.index_close.iloc[i + h]
    signal_close = panel.close[ticker].iloc[i]
    if not all(np.isfinite(v) and v > 0 for v in (entry, exit_, i_entry, i_exit, signal_close)):
        return None
    net = exit_ / entry - 1 - COST
    idx = i_exit / i_entry - 1
    return {"ticker": ticker, "session": panel.cal[i].date().isoformat(), "horizon": h,
            "label_version": LABEL_VERSION, "entry_date": panel.cal[i + 1].date().isoformat(),
            "exit_date": panel.cal[i + h].date().isoformat(), "net_return": float(net), "index_return": float(idx),
            "alpha": float(net - idx), "absolute_label": classify(net, ABS_THRESHOLD[h]),
            "relative_label": classify(net - idx, REL_THRESHOLD[h]),
            "fillable": bool(entry < signal_close * (1 + CEILING))}


def labels_for(panel, tickers: list[str], sessions: list[int]) -> pd.DataFrame:
    rows = [lab for i in sessions for t in tickers for h in HORIZONS if (lab := label(panel, t, i, h))]
    return pd.DataFrame(rows)


def save_labels(conn, df: pd.DataFrame) -> int:
    cols = ["ticker", "session", "horizon", "label_version", "entry_date", "exit_date", "net_return", "index_return",
            "alpha", "absolute_label", "relative_label", "fillable"]
    with conn.cursor() as cur:
        for r in df[cols].itertuples(index=False):
            cur.execute(
                f"INSERT INTO forecast_labels ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                "ON CONFLICT (ticker, session, horizon, label_version) DO NOTHING", tuple(r))
    return len(df)
