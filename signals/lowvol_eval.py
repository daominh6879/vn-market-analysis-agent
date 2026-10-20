"""
signals/lowvol_eval.py — can volatility alone select stocks? Monthly-rebalanced equal-weight portfolios by trailing
volatility quintile among liquid names (20-session avg traded value >= MIN_VALUE_BN), with fees + sell tax on the
traded fraction, vs the equal-weight liquid universe and VNINDEX.

Usage:
    python -m signals.lowvol_eval
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import ledger
from signals.breakout_eval import MIN_VALUE_BN, load
from signals.breakout_portfolio import WARMUP, stats

VOL_WINDOW = 60
QUINTILES = 5


def simulate(daily: np.ndarray, targets: dict[int, np.ndarray], start: int, fee: float, tax: float) -> np.ndarray:
    """targets[t] = weight vector decided at close t, traded at close t (rebalance); positions drift in between."""
    T, N = daily.shape
    w, eq, out = np.zeros(N), 1.0, np.ones(T)
    for d in range(start, T):
        eq *= 1 + float(np.nansum(w * np.nan_to_num(daily[d])))             # return of yesterday's weights
        if w.sum() > 0:
            w = w * (1 + np.nan_to_num(daily[d])) / (1 + float(np.nansum(w * np.nan_to_num(daily[d]))))
        if d in targets:
            new = targets[d]
            buys, sells = np.clip(new - w, 0, None).sum(), np.clip(w - new, 0, None).sum()
            eq *= 1 - buys * fee - sells * (fee + tax)
            w = new
        out[d] = eq
    return out[start:]


def main() -> None:
    wide, index = load()
    C = wide["close"]
    liquid = ((C * wide["volume"] / 1e6).rolling(20, min_periods=15).mean() >= MIN_VALUE_BN).to_numpy()
    daily = C.pct_change(fill_method=None)
    vol = daily.rolling(VOL_WINDOW, min_periods=int(VOL_WINDOW * 0.7)).std().to_numpy()
    d_np = daily.to_numpy()
    fee, tax = ledger.fee_pct(), ledger.SELL_TAX_PCT
    month = C.index.to_period("M")
    rebalance = [i for i in range(WARMUP, len(C)) if i == 0 or month[i] != month[i - 1]]
    targets = {k: {} for k in ["all", "lowvol_q1", "highvol_q5", "ex_top_decile"]}
    for t in rebalance:
        ok = np.flatnonzero(liquid[t] & np.isfinite(vol[t]))
        if len(ok) < 50:
            continue
        v = vol[t, ok]
        rank = pd.Series(v).rank(pct=True).to_numpy()
        for name, sel in (("all", np.ones(len(ok), bool)), ("lowvol_q1", rank <= 1 / QUINTILES),
                          ("highvol_q5", rank > 1 - 1 / QUINTILES), ("ex_top_decile", rank <= 0.9)):
            w = np.zeros(C.shape[1])
            w[ok[sel]] = 1.0 / sel.sum()
            targets[name][t] = w
    dates = C.index[WARMUP:]
    idx = index.to_numpy()
    rows, yearly = [], {}
    curves = {"VNINDEX": idx[WARMUP:] / idx[WARMUP]}
    for name, tg in targets.items():
        curves[name] = simulate(d_np, tg, WARMUP, fee, tax)
    for name, eq in curves.items():
        s = stats(eq, dates)
        rows.append({"portfolio": name, "cagr": s["cagr"], "sharpe": s["sharpe"], "maxdd": s["maxdd"], "total": s["total"]})
        yearly[name] = pd.Series(eq, index=dates).groupby(dates.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1)
    print(f"{dates[0].date()}..{dates[-1].date()}, monthly rebalance, vol window {VOL_WINDOW}, fee {fee:.2%} + sell tax {tax:.1%}")
    print(pd.DataFrame(rows).to_markdown(index=False, floatfmt="+.3f"))
    print("\ncalendar-year return:")
    print(pd.DataFrame(yearly).to_markdown(floatfmt="+.3f"))


if __name__ == "__main__":
    main()
