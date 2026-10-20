"""
portfolio/backtest_rules.py — does the advisor's loss/profit rule beat "do nothing"? (no model signal involved)

Monte Carlo over single positions: a random liquid ticker on a random date, held up to HORIZON sessions.
Every rule set is simulated on the SAME entries as buy-and-hold, so the comparison is paired.
VN realism: fee on both sides + sell tax, shares sellable only from T+2, orders fill at the NEXT close after the
trigger, and a floor-locked session (return <= -LOCK_MOVE) cannot be sold. Cash earns nothing after an exit.

Usage:
    python -m portfolio.backtest_rules [--n 4000] [--horizon 120] [--seed 0]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import ledger

LOCK_MOVE = 0.065              # <= -6.5% daily close-to-close ~ floor-locked on HOSE (-7%): cannot sell that session
SETTLE_LAG = 2                 # sellable from T+2
MIN_AVG_VALUE_BN = 5.0         # same liquidity floor as the advisor
# (name, stop_loss_pct or None, take_profit_pct or None) -- the advisor ships ("advisor(-8,+15)")
RULES = [("hold", None, None), ("stop-5", -5, None), ("stop-8", -8, None), ("stop-12", -12, None),
         ("stop-20", -20, None), ("tp+10", None, 10), ("tp+15", None, 15), ("tp+25", None, 25),
         ("advisor(-8,+15)", -8, 15), ("stop-12,tp+25", -12, 25)]


def simulate(path: np.ndarray, daily: np.ndarray, stop: float | None, take: float | None,
             fee: float, tax: float) -> tuple[float, float, int]:
    """path[t] = close[t]/entry_close (t=0 is the entry session); daily[t] = close[t]/close[t-1]-1.
    Returns (net return, max drawdown of the held position, exit session). Net return includes fee + tax."""
    n = len(path)
    pnl = path / (1 + fee) - 1                                # P/L vs average cost (cost basis includes the buy fee)
    trig = np.zeros(n, dtype=bool)
    if stop is not None:
        trig |= pnl <= stop / 100
    if take is not None:
        trig |= pnl >= take / 100
    trig[:SETTLE_LAG] = False                                 # not yet sellable
    exit_t = n - 1                                            # default: sell at the horizon
    hits = np.flatnonzero(trig)
    if hits.size:
        t = int(hits[0]) + 1                                  # order placed at close, filled at next close
        while t < n - 1 and daily[t] <= -LOCK_MOVE:           # floor-locked: unsellable, try next session
            t += 1
        exit_t = min(t, n - 1)
    held = path[: exit_t + 1]
    dd = float((held / np.maximum.accumulate(held) - 1).min())
    net = path[exit_t] * (1 - fee - tax) / (1 + fee) - 1
    return float(net), dd, exit_t


def load_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    from core.db import get_conn
    with get_conn() as conn:
        px = pd.read_sql_query("SELECT ticker, date, close, volume FROM ohlcv_daily "
                               "WHERE ticker IN (SELECT ticker FROM securities WHERE is_active)", conn)
    px["date"] = pd.to_datetime(px["date"])
    px["close"], px["volume"] = px["close"].astype(float), px["volume"].astype(float)
    close = px.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    value_bn = (px.assign(v=px["close"] * px["volume"] / 1e6)
                .pivot_table(index="date", columns="ticker", values="v", aggfunc="last").sort_index())
    cal = close.index[close.notna().sum(axis=1) >= 0.5 * close.notna().sum(axis=1).max()]
    return close.reindex(cal), value_bn.reindex(cal).rolling(20, min_periods=15).mean()


def run(n: int, horizon: int, seed: int) -> pd.DataFrame:
    close, avg_value = load_prices()
    rng = np.random.default_rng(seed)
    fee, tax = ledger.fee_pct(), ledger.SELL_TAX_PCT
    dates = np.arange(250, len(close) - horizon - 1)           # leave 250 sessions of history, full horizon after
    rows = []
    while len(rows) < n:
        d = int(rng.choice(dates))
        ok = np.flatnonzero((avg_value.iloc[d].to_numpy() >= MIN_AVG_VALUE_BN)
                            & close.iloc[d: d + horizon + 1].notna().all().to_numpy())
        if ok.size == 0:
            continue
        j = int(rng.choice(ok))
        c = close.iloc[d: d + horizon + 1, j].to_numpy()
        path, daily = c / c[0], np.r_[0.0, c[1:] / c[:-1] - 1]
        res = {name: simulate(path, daily, s, t, fee, tax) for name, s, t in RULES}
        rows.append({"entry": close.index[d], "ticker": close.columns[j],
                     **{f"{k}|ret": v[0] for k, v in res.items()}, **{f"{k}|dd": v[1] for k, v in res.items()},
                     **{f"{k}|held": v[2] for k, v in res.items()}})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, n_boot: int = 1000, seed: int = 0) -> pd.DataFrame:
    """Per rule: mean / worst-5% return, mean / worst-5% max drawdown, exit rate, and the paired mean difference vs
    hold with a cluster-bootstrap CI (clusters = entry month: positions entered the same month share a market path)."""
    rng = np.random.default_rng(seed)
    month = df["entry"].dt.to_period("M").astype(str).to_numpy()
    groups = [np.flatnonzero(month == m) for m in np.unique(month)]
    out = []
    for name, *_ in RULES:
        r, h = df[f"{name}|ret"].to_numpy(), df["hold|ret"].to_numpy()
        diff = r - h
        boots = [diff[np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])].mean()
                 for _ in range(n_boot)]
        out.append({"rule": name, "mean_ret": r.mean(), "p5_ret": np.percentile(r, 5),
                    "mean_maxdd": df[f"{name}|dd"].mean(), "p5_maxdd": np.percentile(df[f"{name}|dd"], 5),
                    "exited_early": (df[f"{name}|held"] < df["hold|held"]).mean(),
                    "diff_vs_hold": diff.mean(), "ci_lo": np.percentile(boots, 2.5), "ci_hi": np.percentile(boots, 97.5)})
    return pd.DataFrame(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    df = run(a.n, a.horizon, a.seed)
    print(f"{len(df)} positions, entries {df['entry'].min().date()}..{df['entry'].max().date()}, horizon {a.horizon}")
    print(summarise(df).to_markdown(index=False, floatfmt="+.4f"))
