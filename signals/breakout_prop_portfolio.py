"""
signals/breakout_prop_portfolio.py — MID breakout portfolio gated by proprietary-flow buying (tự doanh > +1% of traded value).

Two exit configs fixed beforehand: the default (hold 20, stop -8%) and the earlier train winner (hold 60, trail 20%).
Idle cash parks in VNINDEX. Compared with VNINDEX and with random liquid picks under the same exits (same dates and counts).
Period starts 2022-07-01 (first full prop window).

Usage:
    python -m signals.breakout_prop_portfolio
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import ledger
from signals.breakout_eval import MIN_VALUE_BN, events, load, signals
from signals.breakout_portfolio import WARMUP, run_portfolio, stats
from signals.breakout_live import prop_ratio

SEEDS = 30
START = pd.Timestamp("2022-07-01")                        # first date with a full 20-session proprietary window
EXITS = {"hold20/stop8": dict(hold=20, trail=None), "hold60/trail20": dict(hold=60, trail=0.20)}


def yearly(eq: np.ndarray, dates: pd.DatetimeIndex) -> dict:
    return pd.Series(eq, index=dates).groupby(dates.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1).round(3).to_dict()


def main() -> None:
    wide, index = load()
    c = wide["close"]
    close, volume = c.to_numpy(), wide["volume"].to_numpy()
    liquid = ((c * wide["volume"] / 1e6).rolling(20, min_periods=15).mean() >= MIN_VALUE_BN)
    ret63 = (c / c.shift(63) - 1).to_numpy()
    fee, tax = ledger.fee_pct(), ledger.SELL_TAX_PCT
    idx = index.to_numpy()
    park = np.r_[0.0, idx[1:] / idx[:-1] - 1]
    start = int(np.searchsorted(c.index, START))
    dates = c.index[start:]
    idx_eq = idx[start:] / idx[start]
    ix = stats(idx_eq, dates)
    print(f"{dates[0].date()}..{dates[-1].date()}  VNINDEX cagr {ix['cagr']:+.1%} dd {ix['maxdd']:.1%} by year {yearly(idx_eq, dates)}\n")
    mid = events(signals(wide, index, "loose")["MID"]) & liquid
    rows = []
    for gate_name, gate in (("MID (no gate)", None), ("MID+prop5>1%", prop_ratio(wide, 5) > 0.01),
                            ("MID+prop20>1%", prop_ratio(wide, 20) > 0.01)):
        ev = (mid if gate is None else mid & gate).to_numpy()
        cand = {t: list(js[np.argsort(-np.nan_to_num(ret63[t, js], nan=-9))])
                for t in np.flatnonzero(ev.any(axis=1)) for js in [np.flatnonzero(ev[t])]}
        liq = liquid.to_numpy()
        for exit_name, kw in EXITS.items():
            eq, trades = run_portfolio(close, volume, cand, fee, tax, start, park, **kw)
            s = stats(eq, dates)
            rc = []
            for seed in range(SEEDS):
                r = np.random.default_rng(seed)
                rcand = {t: list(r.choice(np.flatnonzero(liq[t]), size=len(js), replace=False)) for t, js in cand.items()
                         if liq[t].sum() >= len(js)}
                e, _ = run_portfolio(close, volume, rcand, fee, tax, start, park, **kw)
                rc.append(stats(e, dates)["cagr"])
            rows.append({"strategy": gate_name, "exit": exit_name, "trades": len(trades), "win": np.mean(np.array(trades) > 0),
                         "cagr": s["cagr"], "vs_index": s["cagr"] - ix["cagr"], "sharpe": s["sharpe"], "maxdd": s["maxdd"],
                         "random_mean": np.mean(rc), "beats_random": float(np.mean(s["cagr"] > np.array(rc)))})
            print(f"{gate_name} / {exit_name}: by year {yearly(eq, dates)}", flush=True)
    print()
    print(pd.DataFrame(rows).to_markdown(index=False, floatfmt="+.3f"))


if __name__ == "__main__":
    main()
