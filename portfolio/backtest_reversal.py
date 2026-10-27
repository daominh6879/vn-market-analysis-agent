"""
portfolio/backtest_reversal.py — pre-registered rule (a): does buying VN30's 20-session losers beat holding E1VFVN30?

Registered 27/10/2026 in vn-portfolio docs/notes/strategy-changelog.md BEFORE any data from START was seen; the rule
and the pass mark are not changed afterwards. The Trend Analyzer backtest (1-10/2026) suggested VN30 mean-reverted;
this tests that on new sessions only.
Rule: every STEP sessions from the first session >= START, buy equal-weight the PICKS VN30 tickers with the lowest
20-session return among those with a 20-session average traded value >= 5 tỷ (trend.features); entry at the next
session's open, exit at the close h sessions after the signal, trend.labels.COST both for the basket and the ETF, an
entry at the ceiling is skipped. Excess = basket net - E1VFVN30 net over the same open -> close. The top-PICKS
(momentum) basket is printed beside it for contrast. SE across signal sessions, widened by sqrt(h / STEP).
Pass mark (final read ~2027-03-25, ~100 sessions; first look 2026-12-24): 20-session excess - 2·SE > 0.

Usage:
    python -m portfolio.backtest_reversal [--since 2026-10-28]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trend.labels import CEILING, COST

START = "2026-10-28"
STEP = 5
PICKS = 5
HORIZONS = (5, 20)
BENCHMARK = "E1VFVN30"


def picks(panel, i: int, k: int = PICKS) -> dict[str, list[str]]:
    """{'losers': lowest-k ret20, 'winners': highest-k} among liquid universe tickers on calendar index i."""
    r = panel.ret[20].iloc[i]
    liquid = panel.liquid.iloc[i]
    pool = [t for t in panel.universe if bool(liquid.get(t, False)) and np.isfinite(r.get(t, np.nan))]
    ranked = sorted(pool, key=lambda t: r[t])
    return {"losers": ranked[:k], "winners": ranked[::-1][:k]}


def basket_net(panel, tickers: list[str], i: int, h: int) -> float | None:
    """Equal-weight net return of `tickers` bought at open i+1, sold at close i+h (fillable ones only)."""
    rets = []
    for t in tickers:
        entry, exit_, sig = panel.open[t].iloc[i + 1], panel.close[t].iloc[i + h], panel.close[t].iloc[i]
        if all(np.isfinite(v) and v > 0 for v in (entry, exit_, sig)) and entry < sig * (1 + CEILING):
            rets.append(exit_ / entry - 1 - COST)
    return float(np.mean(rets)) if rets else None


def evaluate(panel, etf: pd.DataFrame, since: str) -> pd.DataFrame:
    """One row per (signal session, horizon) with a closed window: losers / winners / ETF net and the excesses.
    etf: index = date, columns open, close (any unit)."""
    etf = etf.reindex(panel.cal)
    first = panel.cal.searchsorted(pd.Timestamp(since))
    rows = []
    for i in range(first, len(panel.cal) - 1, STEP):
        p = picks(panel, i)
        for h in HORIZONS:
            if i + h >= len(panel.cal):
                continue
            e_open, e_close = etf["open"].iloc[i + 1], etf["close"].iloc[i + h]
            if not (np.isfinite(e_open) and np.isfinite(e_close)):
                continue
            etf_net = e_close / e_open - 1 - COST
            lo, hi = basket_net(panel, p["losers"], i, h), basket_net(panel, p["winners"], i, h)
            rows.append({"session": panel.cal[i].date().isoformat(), "horizon": h, "losers": ",".join(p["losers"]),
                         "losers_net": lo, "winners_net": hi, "etf_net": etf_net,
                         "losers_excess": None if lo is None else lo - etf_net,
                         "winners_excess": None if hi is None else hi - etf_net})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> list[dict]:
    out = []
    for h, g in df.groupby("horizon") if len(df) else []:
        row = {"horizon": int(h), "sessions": int(len(g)), "etf_net_mean": float(g["etf_net"].mean())}
        for k in ("losers_excess", "winners_excess"):
            x = g[k].dropna().astype(float)
            se = x.std(ddof=1) / np.sqrt(len(x)) * np.sqrt(max(1.0, h / STEP)) if len(x) >= 2 else np.nan
            row[k] = float(x.mean()) if len(x) else np.nan
            row[k + "_2se"] = float(2 * se)
        out.append(row)
    return out


def load_etf() -> pd.DataFrame:
    from core.db import get_conn
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT date, open, close FROM benchmark_daily WHERE ticker = %s ORDER BY date", conn,
                               params=(BENCHMARK,))
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").astype(float)


def main(since: str) -> None:
    from trend.features import load_panel
    if since < START:
        print(f"WARNING: since {since} < registered start {START} — in-sample, a mechanics check only, not evidence")
    etf = load_etf()
    if etf.empty:
        raise SystemExit(f"benchmark_daily has no {BENCHMARK} rows yet (Dagster benchmark_ingest)")
    df = evaluate(load_panel(), etf, since)
    if df.empty:
        print(f"no closed window from {since} yet")
        return
    for s in summarise(df):
        pct = lambda m, se: f"{m * 100:+.2f}% ± {se * 100:.2f}" if np.isfinite(se) else f"{m * 100:+.2f}%"  # noqa: E731
        print(f"h={s['horizon']}: {s['sessions']} signals, ETF {s['etf_net_mean'] * 100:+.2f}%/window | losers vs ETF "
              f"{pct(s['losers_excess'], s['losers_excess_2se'])} | winners vs ETF "
              f"{pct(s['winners_excess'], s['winners_excess_2se'])}")
    print("pass mark: h=20 losers excess - 2·SE > 0 at the final read (~2027-03-25)")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=START)
    main(ap.parse_args().since)
