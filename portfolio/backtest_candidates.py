"""
portfolio/backtest_candidates.py — do the advisor's rule-based parts pick stocks that beat VNINDEX? (no LLM involved)

Replays, on past sessions, what the advisor does before the LLM judges:
- the candidate filter (evidence.rank_candidates): liquid (20-session avg value >= MIN_AVG_VALUE_BN), ret_20 within
  CANDIDATE_RET20_RANGE, foreign net buying over 5 sessions, top CANDIDATE_COUNT by foreign net 20 sessions;
- the levels the LLM is shown (tools.levels.find_support_resistance: 3 nearest supports / resistances) and the R:R a
  stop at the nearest support >= 2% below and a target at the farthest listed resistance give — what the AI did on
  TCB/MSN (backlog I-004 / I-009);
- autotrade's chase rule (skip when the next open is > 3% above the signal close).
Signal on session d's close (the advice's data_session), buy at d+1's close, sell at the close h sessions later; excess
= basket return - VNINDEX return over the same closes, after fee both sides + sell tax. One basket per signal date, so
the SE is across dates; dates are STEP sessions apart and the SE of an h > STEP horizon is widened by sqrt(h / STEP)
for the overlapping windows.

Usage:
    python -m portfolio.backtest_candidates [--step 5]
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
from portfolio.evidence import CANDIDATE_COUNT, CANDIDATE_RET20_RANGE, MIN_AVG_VALUE_BN

HORIZONS = (5, 20)
CHASE_PCT = 3.0                  # autotrade.MAX_CHASE_PCT in vn-portfolio
MIN_STOP_PCT = 2.0               # advisor.MIN_STOP_PCT: an AI stop sits at least 2% below the price
LEVEL_SESSIONS = 250             # history the technical block computes levels from (~365 calendar days)


def load() -> dict:
    from core.db import get_conn
    with get_conn() as conn:
        px = pd.read_sql_query("SELECT ticker, date, open, high, low, close, volume FROM ohlcv_daily "
                               "WHERE ticker IN (SELECT ticker FROM securities WHERE is_active)", conn)
        ff = pd.read_sql_query("SELECT ticker, date, net_value FROM foreign_flows", conn)
        idx = pd.read_sql_query("SELECT date, close FROM market_index_daily WHERE index_code = 'VNINDEX'", conn)
    for df in (px, ff, idx):
        df["date"] = pd.to_datetime(df["date"])
    wide = {c: px.pivot_table(index="date", columns="ticker", values=c, aggfunc="last").astype(float).sort_index()
            for c in ("open", "high", "low", "close", "volume")}
    cal = wide["close"].index[wide["close"].notna().sum(axis=1) >= 0.5 * wide["close"].notna().sum(axis=1).max()]
    cal = cal.intersection(idx["date"])
    wide = {k: v.reindex(cal) for k, v in wide.items()}
    value_bn = (wide["close"] * wide["volume"] / 1e6).rolling(20, min_periods=15).mean()
    flows = ff.pivot_table(index="date", columns="ticker", values="net_value", aggfunc="sum").reindex(cal).fillna(0.0)
    return {**wide, "cal": cal, "value_bn": value_bn, "f5": flows.rolling(5).sum(), "f20": flows.rolling(20).sum(),
            "ret20": wide["close"] / wide["close"].shift(20) - 1,
            "index": idx.set_index("date")["close"].astype(float).reindex(cal)}


def baskets(d: dict, i: int) -> dict[str, list[str]]:
    """Ticker baskets on signal session i."""
    v, r, f5, f20 = d["value_bn"].iloc[i], d["ret20"].iloc[i], d["f5"].iloc[i], d["f20"].iloc[i]
    lo, hi = CANDIDATE_RET20_RANGE
    liquid = v.index[(v >= MIN_AVG_VALUE_BN) & r.notna()]
    trend = [t for t in liquid if lo <= r[t] <= hi]
    passing = [t for t in trend if f5.get(t, 0) > 0]
    top = sorted(passing, key=lambda t: -f20.get(t, 0))[:CANDIDATE_COUNT]
    return {"liquid": list(liquid), "ret20_filter": trend, "ret20+foreign5": passing, "candidates_top5": top,
            "top5_by_ret20": sorted(trend, key=lambda t: -r[t])[:CANDIDATE_COUNT]}


def levels_rr(d: dict, t: str, i: int) -> float | None:
    """R:R of stop = nearest listed support >= MIN_STOP_PCT below, target = farthest listed resistance, at close i."""
    from tools.levels import find_support_resistance
    sl = slice(max(0, i - LEVEL_SESSIONS + 1), i + 1)
    df = pd.DataFrame({c: d[c][t].iloc[sl] * 1000 for c in ("high", "low", "close")}).dropna()
    res = find_support_resistance(df)
    if res.status != "ok":
        return None
    price = res.data["close"]
    stops = [s for s in res.data["supports"] if s <= price * (1 - MIN_STOP_PCT / 100)]
    if not stops or not res.data["resistances"]:
        return None
    return (max(res.data["resistances"]) - price) / (price - stops[0])


def run(step: int) -> pd.DataFrame:
    d = load()
    cost = ledger.fee_pct() * 2 + ledger.SELL_TAX_PCT
    close, opn, index = d["close"], d["open"], d["index"]
    rows = []
    for i in range(21, len(d["cal"]) - 1 - max(HORIZONS), step):
        b = baskets(d, i)
        e = i + 1                                                    # bought at the next session's close
        for name, tickers in b.items():
            for h in HORIZONS:
                ret = (close.iloc[e + h][tickers] / close.iloc[e][tickers] - 1).dropna()
                if ret.empty:
                    continue
                rows.append({"date": d["cal"][i], "basket": name, "h": h, "n": len(ret),
                             "excess": ret.mean() - cost - (index.iloc[e + h] / index.iloc[e] - 1)})
        for t in b["candidates_top5"]:                               # per-candidate splits
            rr = levels_rr(d, t, i)
            gap = (opn[t].iloc[e] / close[t].iloc[i] - 1) * 100
            for h in HORIZONS:
                r = close[t].iloc[e + h] / close[t].iloc[e] - 1
                if np.isnan(r):
                    continue
                x = r - cost - (index.iloc[e + h] / index.iloc[e] - 1)
                rows.append({"date": d["cal"][i], "basket": "cand rr<1" if rr is not None and rr < 1 else
                             "cand rr>=1" if rr is not None else "cand rr n/a", "h": h, "n": 1, "excess": x})
                rows.append({"date": d["cal"][i], "basket": f"cand gap>{CHASE_PCT:g}%" if gap > CHASE_PCT
                             else f"cand gap<={CHASE_PCT:g}%", "h": h, "n": 1, "excess": x})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, step: int) -> pd.DataFrame:
    """Per basket and horizon: dates, stocks, mean excess vs VNINDEX per date, its SE across dates (overlap-widened),
    hit rate, and the paired difference vs the equal-weight liquid universe on the same dates (stock-picking skill,
    separate from the index being carried by a few large caps)."""
    def se(x: pd.Series, h: int) -> float:
        return x.std(ddof=1) / np.sqrt(len(x)) * np.sqrt(max(1.0, h / step)) if len(x) > 1 else np.nan
    out = []
    for (name, h), g in df.groupby(["basket", "h"], sort=False):
        per_date = g.groupby("date")["excess"].mean()
        base = df[(df["basket"] == "liquid") & (df["h"] == h)].set_index("date")["excess"]
        diff = (per_date - base.reindex(per_date.index)).dropna()
        out.append({"basket": name, "h": h, "dates": len(per_date), "items": int(g["n"].sum()),
                    "excess_vs_index_pct": per_date.mean() * 100, "se_pct": se(per_date, h) * 100,
                    "hit_rate": (per_date > 0).mean(),
                    "vs_liquid_pct": diff.mean() * 100, "vs_liquid_lo_2se": (diff.mean() - 2 * se(diff, h)) * 100,
                    "vs_liquid_hi_2se": (diff.mean() + 2 * se(diff, h)) * 100})
    return pd.DataFrame(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    df = run(a.step)
    print(f"signal dates {df['date'].min().date()}..{df['date'].max().date()}, step {a.step}")
    print(summarise(df, a.step).to_markdown(index=False, floatfmt="+.2f"))
