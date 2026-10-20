"""
signals/breakout_eval.py — historical test of tools/breakout.py (SHORT / MID / LONG breakout rules).

The production scanner reads one ticker at a time from the DB, so the rules are re-implemented here, vectorized
over the whole panel. Differences from the scanner (documented, tested via two variants):
  - volume thresholds are fixed: "strict" = MACD-not-bullish thresholds, "loose" = MACD-bullish thresholds
    (the scanner picks one per ticker/day from MACD and foreign flow; the truth lies between the two);
  - the foreign-flow adaptation is not modelled.
An event = first signal of a ticker within a 10-session window. Entry at the NEXT close after the signal (no
look-ahead), exit HORIZON sessions later. Liquid names only (20-session avg traded value >= MIN_VALUE_BN).
Reported per type: mean excess vs VNINDEX, mean excess vs the same-day liquid universe, hit rate, net-of-cost
return, and a cluster bootstrap (month) CI on the vs-universe difference.

Usage:
    python -m signals.breakout_eval
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

MIN_VALUE_BN = 5.0
ROUND_TRIP_COST = 0.004            # fee 0.15% x2 + sell tax 0.1%
HORIZONS = (5, 10, 20)
COOLDOWN = 10
THRESHOLDS = {"strict": {"SHORT": 1.5, "MID": 1.8, "LONG": 2.0}, "loose": {"SHORT": 1.2, "MID": 1.5, "LONG": 1.6}}


def load() -> tuple[dict[str, pd.DataFrame], pd.Series]:
    from core.db import get_conn
    with get_conn() as conn:
        px = pd.read_sql_query("SELECT ticker, date, open, high, low, close, volume FROM ohlcv_daily "
                               "WHERE ticker IN (SELECT ticker FROM securities WHERE is_active)", conn)
        idx = pd.read_sql_query("SELECT date, close FROM market_index_daily WHERE index_code = 'VNINDEX'", conn)
    px["date"] = pd.to_datetime(px["date"])
    wide = {c: px.pivot_table(index="date", columns="ticker", values=c, aggfunc="last").sort_index().astype(float)
            for c in ("open", "high", "low", "close", "volume")}
    cal = wide["close"].index[wide["close"].notna().sum(axis=1) >= 0.5 * wide["close"].notna().sum(axis=1).max()]
    wide = {k: v.reindex(cal) for k, v in wide.items()}
    wide["close"] = wide["close"].ffill()
    wide["volume"] = wide["volume"].fillna(0.0)
    index = idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"].astype(float).sort_index().reindex(cal).ffill()
    return wide, index


def distribution_days(close: pd.DataFrame, volume: pd.DataFrame, base: int) -> pd.DataFrame:
    """Count of days in the last `base` sessions with a >=0.2% drop on above-average volume (scanner definition:
    the average is over the base+1 closes ending today, today included)."""
    out = np.zeros(close.shape)
    pct = (close / close.shift(1) - 1).to_numpy()
    for j in range(close.shape[1]):
        v, p = volume.iloc[:, j].to_numpy(), pct[:, j]
        if len(v) <= base + 1:
            continue
        vw = sliding_window_view(v, base + 1)                     # window ending at t = index t - base
        pw = sliding_window_view(np.nan_to_num(p), base + 1)[:, 1:]
        out[base:, j] = ((pw <= -0.002) & (vw[:, 1:] > vw.mean(axis=1, keepdims=True))).sum(axis=1)
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def signals(wide: dict[str, pd.DataFrame], index: pd.Series, variant: str) -> dict[str, pd.DataFrame]:
    O, H, L, C, V = (wide[k] for k in ("open", "high", "low", "close", "volume"))
    th = THRESHOLDS[variant]
    prev = C.shift(1)
    ma20, ma60 = prev.rolling(20).mean(), prev.rolling(60).mean()
    ma20_entry = (prev < ma20 * 0.995) & (C > ma20)
    far_from_low = C > L.rolling(252, min_periods=1).min() * 2.0
    strong = C > O
    mkt_up = ((index.rolling(20).mean() > index.rolling(60).mean() * 0.97)).to_numpy()[:, None]
    mr = (index / index.shift(63) - 1).to_numpy()[:, None]
    sr = (C / C.shift(63) - 1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs_ok = np.where(mr == 0, 1.0, sr / mr) >= 0.85

    def base_stats(n: int):
        bl, bh = L.shift(1).rolling(n).min(), H.shift(1).rolling(n).max()
        return (bh - bl) / bl, C.shift(1).rolling(n).max()

    out = {}
    # SHORT: 20d base, pivot = max of the previous 10 closes
    rng, _ = base_stats(20)
    pivot = prev.rolling(10).max()
    vol_ok = V >= V.shift(1).rolling(20).mean() * (th["SHORT"] + 0.3 * far_from_low)
    brk = ((C > pivot) & strong & vol_ok) | ma20_entry
    out["SHORT"] = brk & (rng < 0.15) & (distribution_days(C, V, 20) <= 3) & (ma20 > ma60 * 0.98) & mkt_up & rs_ok
    # MID: 40d base
    rng, pivot = base_stats(40)
    vol_ok = V >= V.shift(1).rolling(40).mean() * (th["MID"] + 0.3 * far_from_low)
    brk = ((C > pivot) & strong & vol_ok) | ma20_entry
    out["MID"] = brk & (rng < 0.20) & (distribution_days(C, V, 40) <= 6) & (ma20 > ma60 * 0.98) & mkt_up & rs_ok
    # LONG: 100d base with volume contraction
    rng, pivot = base_stats(100)
    avg20, avg80 = V.shift(1).rolling(20).mean(), V.shift(1).rolling(80).mean()
    vol_ok = V >= avg20 * (th["LONG"] + 0.3 * far_from_low)
    brk = (rng < 0.35) & ((avg20 < avg80 * 0.8) | ma20_entry) & (C > pivot) & strong & vol_ok
    out["LONG"] = brk & (distribution_days(C, V, 100) <= 10) & (ma20 > ma60) & mkt_up & rs_ok
    out["ANY"] = out["SHORT"] | out["MID"] | out["LONG"]
    return {k: v.fillna(False) for k, v in out.items()}


def events(sig: pd.DataFrame) -> pd.DataFrame:
    """First signal of a ticker inside a COOLDOWN-session window."""
    prior = sig.shift(1).astype(float).rolling(COOLDOWN - 1, min_periods=1).max().fillna(0) > 0
    return sig & ~prior


def evaluate(wide: dict[str, pd.DataFrame], index: pd.Series, ev: pd.DataFrame, liquid: pd.DataFrame,
             horizon: int) -> pd.DataFrame:
    C = wide["close"]
    entry, exit_ = C.shift(-1), C.shift(-1 - horizon)
    ret = exit_ / entry - 1
    idx_ret = index.shift(-1 - horizon) / index.shift(-1) - 1
    excess = ret.sub(idx_ret, axis=0)
    universe = excess.where(liquid).mean(axis=1)
    mask = ev & liquid & ret.notna()
    s = ret.where(mask).stack()
    df = pd.DataFrame({"ret": s, "excess": excess.where(mask).stack()})
    df["vs_universe"] = df["excess"] - universe.reindex(df.index.get_level_values(0)).to_numpy()
    return df


def cluster_ci(df: pd.DataFrame, col: str, n_boot: int = 1000, seed: int = 0) -> tuple[float, float]:
    month = df.index.get_level_values(0).to_period("M").astype(str).to_numpy()
    groups = [np.flatnonzero(month == m) for m in np.unique(month)]
    x = df[col].to_numpy()
    rng = np.random.default_rng(seed)
    means = [x[np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])].mean() for _ in range(n_boot)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> None:
    wide, index = load()
    value = (wide["close"] * wide["volume"] / 1e6).rolling(20, min_periods=15).mean()
    liquid = value >= MIN_VALUE_BN
    rows = []
    for variant in THRESHOLDS:
        sigs = signals(wide, index, variant)
        for name, sig in sigs.items():
            ev = events(sig)
            for h in HORIZONS:
                df = evaluate(wide, index, ev, liquid, h)
                if df.empty:
                    continue
                lo, hi = cluster_ci(df, "vs_universe")
                rows.append({"variant": variant, "type": name, "h": h, "n": len(df),
                             "excess_vs_index": df["excess"].mean(), "vs_universe": df["vs_universe"].mean(),
                             "ci_lo": lo, "ci_hi": hi, "hit": (df["excess"] > 0).mean(),
                             "net_ret": df["ret"].mean() - ROUND_TRIP_COST})
    res = pd.DataFrame(rows)
    print(res.to_markdown(index=False, floatfmt="+.4f"))
    # consistency across periods for the combined signal, 20-session horizon, loose variant
    ev = events(signals(wide, index, "loose")["ANY"])
    df = evaluate(wide, index, ev, liquid, 20)
    df["period"] = df.index.get_level_values(0).year
    print("\nANY / loose / 20 sessions by entry year:")
    print(df.groupby("period").agg(n=("ret", "size"), excess=("excess", "mean"), vs_universe=("vs_universe", "mean"),
                                   hit=("excess", lambda x: (x > 0).mean())).to_markdown(floatfmt="+.4f"))
    print(f"\nliquid stock-days: {int(liquid.sum().sum())}, universe mean excess is the zero line for 'vs_universe'")


if __name__ == "__main__":
    main()
