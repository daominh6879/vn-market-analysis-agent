"""
signals/breakout_monitor.py — realized outcome of logged breakout events (forward test).

Primary comparison: ALL breakout types (SHORT/MID/LONG, one event per ticker per COOLDOWN sessions, like
signals/breakout_eval.events), prop-gated vs ungated, excess return vs the same-day liquid universe. Entry = next
close, exit HORIZON sessions later, as in the backtest. MID-only numbers are reported alongside (keys prefixed mid_).
Decision rule fixed in docs/plans/prop-flow-forward-test.md.

Usage:
    python -m signals.breakout_monitor
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from signals.breakout_eval import COOLDOWN, MIN_VALUE_BN, ROUND_TRIP_COST, load

HORIZON = 20
MIN_EVENTS = 30            # below this the comparison is noise; the asset check passes with a note


def _dedupe(sig: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """Keep a row only if the same ticker has no kept row in the previous COOLDOWN-1 sessions (types merge into one event)."""
    sig = sig.assign(pos=calendar.get_indexer(sig["date"])).sort_values(["ticker", "pos"])
    keep, last = [], {}
    for t, p in zip(sig["ticker"], sig["pos"]):
        ok = t not in last or p - last[t] >= COOLDOWN
        keep.append(ok)
        if ok:
            last[t] = p
    return sig.loc[keep]           # not sig[keep]: an empty list there selects zero columns, dropping "date"


def _summarise(part: pd.DataFrame, prefix: str) -> dict:
    out = {}
    for name, rows in (("gated", part[part["prop_gate"]]), ("ungated", part[~part["prop_gate"]])):
        out[f"{prefix}{name}_n"] = int(len(rows))
        out[f"{prefix}{name}_vs_universe"] = float(rows["vs_universe"].mean()) if len(rows) else None
        out[f"{prefix}{name}_net_ret"] = float(rows["ret"].mean() - ROUND_TRIP_COST) if len(rows) else None
    return out


def realized(horizon: int = HORIZON) -> dict:
    from core.db import get_conn
    wide, index = load()
    C = wide["close"]
    liquid = (C * wide["volume"] / 1e6).rolling(20, min_periods=15).mean() >= MIN_VALUE_BN
    ret = C.shift(-1 - horizon) / C.shift(-1) - 1
    excess = ret.sub(index.shift(-1 - horizon) / index.shift(-1) - 1, axis=0)
    universe = excess.where(liquid).mean(axis=1)
    with get_conn() as conn:
        sig = pd.read_sql_query("SELECT ticker, date, signal_type, prop_gate FROM breakout_signals", conn)
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["date"].isin(C.index)]
    sig = _dedupe(sig, C.index)
    sig["ret"] = [ret.at[d, t] for d, t in zip(sig["date"], sig["ticker"])]
    sig["vs_universe"] = [excess.at[d, t] - universe.at[d] for d, t in zip(sig["date"], sig["ticker"])]
    done = sig.dropna(subset=["ret"])
    return {"logged": int(len(sig)), "matured": int(len(done)), **_summarise(done, ""),
            **_summarise(done[done["signal_type"] == "MID"], "mid_")}


if __name__ == "__main__":
    for k, v in realized().items():
        print(f"{k}: {v}")
