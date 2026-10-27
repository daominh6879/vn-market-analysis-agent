"""
trend/calibration.py — measured frequencies instead of LLM-declared probabilities (plan §6.4, §7.4).

For one variant and horizon, P(up | label) = (ups + 1) / (n + 2) over PAST forecasts whose outcome is known, where
up = net return > 0 (absolute) or alpha > 0 (relative). A label seen fewer than MIN_SAMPLES times is not calibrated:
probability None, status 'not_calibrated'. The backtest builds the table walk-forward (only forecasts whose label had
completed by the test session); the live service builds it from every labelled forecast of the variant.
"""
from __future__ import annotations

import pandas as pd

MIN_SAMPLES = 30
TRENDS = ("bullish", "neutral", "bearish")


def table(history: pd.DataFrame) -> dict:
    """history rows: horizon, absolute_trend, relative_trend_vs_vnindex, net_return, alpha (abstentions dropped).
    -> {(horizon, 'abs'|'rel', trend): {n, p_up}}; plus (horizon, kind, '*') = the base rate."""
    out = {}
    for (h,), d in history.groupby(["horizon"]):
        for kind, col, ret in (("abs", "absolute_trend", "net_return"), ("rel", "relative_trend_vs_vnindex", "alpha")):
            dd = d[d[col].isin(TRENDS)]
            out[(h, kind, "*")] = {"n": len(dd), "p_up": ((dd[ret] > 0).sum() + 1) / (len(dd) + 2)}
            for trend, g in dd.groupby(col):
                out[(h, kind, trend)] = {"n": len(g), "p_up": ((g[ret] > 0).sum() + 1) / (len(g) + 2)}
    return out


def lookup(tab: dict, h: int, kind: str, trend: str) -> tuple[float | None, str, int]:
    """-> (probability, status, samples)."""
    cell = tab.get((h, kind, trend))
    if not cell or cell["n"] < MIN_SAMPLES:
        return None, "not_calibrated", cell["n"] if cell else 0
    return float(cell["p_up"]), "calibrated", cell["n"]


def to_json(tab: dict) -> list[dict]:
    return [{"horizon": h, "kind": k, "trend": t, **v} for (h, k, t), v in sorted(tab.items(), key=str)]
