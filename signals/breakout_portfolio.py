"""
signals/breakout_portfolio.py — portfolio backtest of the breakout screen (tools/breakout.py rules) vs random picks.

Rules (long only, one account): a breakout signal at close t is bought at close t+1 (skipped when that session is
ceiling-locked, volume 0 or the stock is not liquid); at most MAX_POS positions of 1/MAX_POS of equity each, best
63-session return first. Exit: stop-loss at STOP% vs cost basis (sellable from T+2, order fills the next close,
floor-locked sessions cannot be sold) or after HOLD sessions. Fee on both sides + sell tax; idle cash earns nothing.

Baseline: the SAME number of entries each day but random liquid tickers, same exits, RANDOM_SEEDS repetitions, so
the question is "does the breakout filter beat picking at random under identical rules?", not "did the market rise?".

Usage:
    python -m signals.breakout_portfolio
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

MAX_POS = 10
HOLD = 20
STOP = -0.08
SETTLE = 2
LOCK = 0.065                   # |daily move| >= 6.5%: ceiling (cannot buy) / floor (cannot sell) lock
WARMUP = 260
RANDOM_SEEDS = 30


def run_portfolio(close: np.ndarray, volume: np.ndarray, cand: dict[int, list[int]], fee: float, tax: float,
                  start: int, park: np.ndarray | None = None, hold: int = HOLD, trail: float | None = None,
                  ma: np.ndarray | None = None) -> tuple[np.ndarray, list[float]]:
    """cand[t] = tickers signalled at close t, best first. Returns (daily equity from `start`, trade returns).
    park = daily index returns: idle cash is held in the index (fee on each move in/out, sell tax on the way out).
    Exits (all fill at the next close): stop-loss STOP vs cost, trailing stop `trail` from the highest close since
    entry, close below `ma` (a close-vs-MA20 matrix), or `hold` sessions."""
    T = close.shape[0]
    daily = np.zeros_like(close)
    daily[1:] = close[1:] / close[:-1] - 1
    cash, pos, trades, equity = 1.0, [], [], np.ones(T)
    for d in range(start, T):
        if park is not None:
            cash *= 1 + park[d]
        keep = []
        for p in pos:                                               # exits first
            j = p["j"]
            price = close[d, j]
            p["peak"] = max(p["peak"], price)
            due = p["pending"] or d - p["entry"] >= hold
            if not due and d - p["entry"] >= SETTLE and (
                    price / p["cost"] - 1 <= STOP
                    or (trail is not None and price / p["peak"] - 1 <= -trail)
                    or (ma is not None and price < ma[d, j])):
                p["pending"] = True                                 # order placed at this close, fills at the next
                due = False
            if due and daily[d, j] > -LOCK and d > p["entry"]:
                cash += p["shares"] * price * (1 - fee - tax) * ((1 - fee) if park is not None else 1.0)
                trades.append(p["shares"] * price * (1 - fee - tax) / (p["shares"] * p["cost"]) - 1)
            else:
                keep.append(p)
        pos = keep
        held = {p["j"] for p in pos}
        eq = cash + sum(p["shares"] * close[d, p["j"]] for p in pos)
        free = MAX_POS - len(pos)
        for j in cand.get(d - 1, []):                               # signal at d-1 close -> buy at d close
            if free <= 0:
                break
            if j in held or volume[d, j] <= 0 or not np.isfinite(close[d, j]) or daily[d, j] >= LOCK:
                continue
            out_cost = (1 - fee - tax) if park is not None else 1.0       # cash must cover the index sale cost
            alloc = min(eq / MAX_POS, cash * out_cost)
            if alloc <= 0:
                break
            price = close[d, j]
            pos.append({"j": j, "entry": d, "shares": alloc / (price * (1 + fee)), "cost": price * (1 + fee), "pending": False, "peak": price})
            cash -= alloc / out_cost
            held.add(j)
            free -= 1
        equity[d] = cash + sum(p["shares"] * close[d, p["j"]] for p in pos)
    return equity[start:], trades


def stats(eq: np.ndarray, dates: pd.DatetimeIndex) -> dict:
    r = eq[1:] / eq[:-1] - 1
    years = (dates[-1] - dates[0]).days / 365.25
    return {"total": eq[-1] / eq[0] - 1, "cagr": (eq[-1] / eq[0]) ** (1 / years) - 1,
            "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0,
            "maxdd": float((eq / np.maximum.accumulate(eq) - 1).min())}


def by_year(eq: np.ndarray, dates: pd.DatetimeIndex) -> pd.Series:
    s = pd.Series(eq, index=dates)
    return s.groupby(s.index.year).apply(lambda x: x.iloc[-1] / x.iloc[0] - 1)


def main() -> None:
    wide, index = load()
    close_df, volume = wide["close"], wide["volume"].to_numpy()
    close = close_df.to_numpy()
    liquid = ((close_df * wide["volume"] / 1e6).rolling(20, min_periods=15).mean() >= MIN_VALUE_BN).to_numpy()
    ret63 = (close_df / close_df.shift(63) - 1).to_numpy()
    fee, tax = ledger.fee_pct(), ledger.SELL_TAX_PCT
    dates = close_df.index[WARMUP:]
    idx_eq = (index / index.iloc[WARMUP]).to_numpy()[WARMUP:]
    rows, yearly = [], {"VNINDEX": by_year(idx_eq, dates)}
    park = np.r_[0.0, index.to_numpy()[1:] / index.to_numpy()[:-1] - 1]
    sg = signals(wide, index, "loose")
    for label, sig, pk in [("MID", sg["MID"], None), ("MID+idx", sg["MID"], park),
                           ("MID+SHORT+idx", sg["MID"] | sg["SHORT"], park)]:
        ev = (events(sig) & liquid).to_numpy()
        cand = {}
        for t in np.flatnonzero(ev.any(axis=1)):
            js = np.flatnonzero(ev[t])
            cand[t] = list(js[np.argsort(-np.nan_to_num(ret63[t, js], nan=-9))])
        eq, trades = run_portfolio(close, volume, cand, fee, tax, WARMUP, pk)
        s = stats(eq, dates)
        rand_stats = []
        rand_eq = []
        for seed in range(RANDOM_SEEDS):
            r = np.random.default_rng(seed)
            rc = {t: list(r.choice(np.flatnonzero(liquid[t]), size=len(js), replace=False)) for t, js in cand.items()
                  if liquid[t].sum() >= len(js)}
            e, _ = run_portfolio(close, volume, rc, fee, tax, WARMUP, pk)
            rand_stats.append(stats(e, dates))
            rand_eq.append(e)
        rc_cagr = np.array([x["cagr"] for x in rand_stats])
        rows.append({"strategy": label, "entries": len(trades), "win_rate": np.mean(np.array(trades) > 0),
                     "total": s["total"], "cagr": s["cagr"], "sharpe": s["sharpe"], "maxdd": s["maxdd"],
                     "random_cagr_mean": rc_cagr.mean(), "random_cagr_p5": np.percentile(rc_cagr, 5),
                     "random_cagr_p95": np.percentile(rc_cagr, 95), "beats_random": float((rc_cagr < s["cagr"]).mean()),
                     "random_maxdd": np.mean([x["maxdd"] for x in rand_stats])})
        yearly[label] = by_year(eq, dates)
        yearly[f"{label} random"] = by_year(np.mean(rand_eq, axis=0), dates)
    ix = stats(idx_eq, dates)
    print(f"{dates[0].date()}..{dates[-1].date()}, MAX_POS={MAX_POS}, hold<={HOLD}, stop {STOP:.0%}, fee {fee:.2%}+tax {tax:.1%}")
    print(f"VNINDEX buy&hold: total {ix['total']:+.1%} cagr {ix['cagr']:+.1%} sharpe {ix['sharpe']:.2f} maxdd {ix['maxdd']:.1%}\n")
    print(pd.DataFrame(rows).to_markdown(index=False, floatfmt="+.3f"))
    print("\ncalendar-year return:")
    print(pd.DataFrame(yearly).to_markdown(floatfmt="+.3f"))


if __name__ == "__main__":
    main()
