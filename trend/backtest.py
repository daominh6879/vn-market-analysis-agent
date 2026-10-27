"""
trend/backtest.py — walk-forward evaluation of the Trend Judge against baselines (plan §7).

replay: every STEP sessions from WARMUP on, for each universe ticker, build the snapshot from data up to that close
and the events available at as_of (15:15), then forecast with
  A rules, B logistic (refit on labels complete by that session), C LLM + events, D quant + LLM.
All go to forecast_runs (mode 'backtest'); a rerun skips what is stored, so an interrupted replay resumes.
evaluate: label every forecast (trend.labels), calibrate walk-forward (only forecasts labelled before the test
session), and score per variant x horizon x absolute/relative:
  abstain rate; macro F1 / balanced accuracy / directional accuracy on the non-abstained;
  Brier and log loss of the calibrated P(up) vs the past base rate (climatology);
  a long basket of 'bullish' names per session: mean alpha vs VNINDEX after costs, ± 2·SE across sessions
  (widened by sqrt(h / STEP) for overlapping windows), and the same for 'bearish' names.
Reported for the whole period and the HOLDOUT (last quarter of sessions), stored in backtest_runs.
Prompt, thresholds and features were fixed before the first replay; nothing is tuned on these results.

Usage:
    python -m trend.backtest --replay [--variants A,B,C,D] [--workers 8]
    python -m trend.backtest --evaluate
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trend import baselines, calibration, judge
from trend.events import load_events
from trend.features import FEATURE_VERSION, MIN_HISTORY, load_panel
from trend.labels import HORIZONS, LABEL_VERSION, labels_for, save_labels

STEP = 5
WARMUP = MIN_HISTORY
HOLDOUT_SHARE = 0.25
VERSIONS = {"A": baselines.RULES_VERSION, "B": baselines.LOGIT_VERSION, "C": judge.PROMPT_VERSION,
            "D": judge.PROMPT_VERSION}
TRENDS = calibration.TRENDS


def test_sessions(panel) -> list[int]:
    return list(range(WARMUP, len(panel.cal) - 1 - min(HORIZONS), STEP))


def _stored(cur, variant: str) -> set[tuple[str, str]]:
    cur.execute("SELECT ticker, session::text FROM forecast_runs WHERE mode = 'backtest' AND variant = %s "
                "AND prompt_version = %s", (variant, VERSIONS[variant]))
    return set(cur.fetchall())


def _train_frame(snaps: dict, labels: pd.DataFrame, panel, i_test: int) -> pd.DataFrame:
    """B's training rows: past snapshots whose label had completed by session i_test (exit_date <= its date)."""
    test_day = panel.cal[i_test].date().isoformat()
    lab = labels[labels["exit_date"] <= test_day]
    rows = []
    for r in lab.itertuples(index=False):
        s = snaps.get((r.ticker, r.session))
        if s:
            rows.append(dict(zip(baselines.NUMERIC, baselines.vector(s)), horizon=r.horizon,
                             absolute_label=r.absolute_label, relative_label=r.relative_label,
                             net_return=r.net_return, alpha=r.alpha))
    return pd.DataFrame(rows)


def replay(variants: list[str], workers: int) -> None:
    from core.db import get_conn
    panel = load_panel()
    sessions = test_sessions(panel)
    tickers = panel.universe
    print(f"replay: {len(sessions)} sessions {panel.cal[sessions[0]].date()}..{panel.cal[sessions[-1]].date()}, "
          f"{len(tickers)} tickers, variants {variants}", flush=True)
    snaps = {(t, panel.cal[i].date().isoformat()): panel.snapshot(t, panel.cal[i]) for i in sessions for t in tickers}
    labels = labels_for(panel, tickers, sessions)
    with get_conn() as conn:
        save_labels(conn, labels)
        cur = conn.cursor()
        stored = {v: _stored(cur, v) for v in variants}
        events = {k: load_events(cur, k[0], judge.as_of_for(k[1])) for k in snaps} if {"C", "D"} & set(variants) else {}

    for v in [v for v in variants if v in ("A", "B")]:
        n = 0
        with get_conn() as conn:
            for i in sessions:
                day = panel.cal[i].date().isoformat()
                model = baselines.Logit().fit(_train_frame(snaps, labels, panel, i)) if v == "B" else None
                for t in tickers:
                    if (t, day) in stored[v]:
                        continue
                    snap = snaps[(t, day)]
                    out = baselines.rules(snap) if v == "A" else model.predict(snap)
                    inp = {"ticker": t, "as_of": judge.as_of_for(day).isoformat(), "horizons": list(HORIZONS),
                           "features": snap["features"], "signals": snap["signals"]}
                    judge.save_run(conn, t, day, v, "backtest", inp, {"output": out, "valid": True},
                                   prompt_version=VERSIONS[v], feature_version=FEATURE_VERSION)
                    n += 1
        print(f"{v}: {n} forecasts stored", flush=True)

    for v in [v for v in variants if v in ("C", "D")]:
        todo = [k for k in snaps if k not in stored[v]]
        print(f"{v}: {len(todo)} to judge", flush=True)

        def run(key, v=v):
            inp = judge.build_input(snaps[key], events[key], v)
            try:
                res = judge.judge(inp, v)
            except Exception as exc:              # provider error: leave unstored, the next replay retries it
                return key, None, f"{type(exc).__name__}: {exc}"[:200]
            with get_conn() as conn:
                judge.save_run(conn, key[0], key[1], v, "backtest", inp, res, feature_version=FEATURE_VERSION)
            return key, res, None
        done = calls = invalid = failed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, res, err in pool.map(run, todo):
                done += 1
                failed += err is not None
                if res:
                    calls += res.get("called", True)
                    invalid += not res["valid"]
                if err or done % 100 == 0:
                    print(f"  {v} {done}/{len(todo)} (calls {calls}, invalid {invalid}, failed {failed})"
                          + (f" — {err}" if err else ""), flush=True)
        print(f"{v}: done {done}, LLM calls {calls}, invalid {invalid}, failed {failed}", flush=True)


# ── evaluation ────────────────────────────────────────────────────────────────────────────────────────────────────

def load_forecasts(variants: list[str]) -> pd.DataFrame:
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT variant, ticker, session::text, output, valid FROM forecast_runs WHERE mode = 'backtest' "
                    "AND variant = ANY(%s) AND prompt_version = ANY(%s)", (variants, list(set(VERSIONS.values()))))
        rows = []
        for v, t, s, out, valid in cur.fetchall():
            for f in out["forecasts"]:
                rows.append({"variant": v, "ticker": t, "session": s, "horizon": f["horizon_trading_days"],
                             "absolute_trend": f["absolute_trend"], "relative_trend_vs_vnindex": f["relative_trend_vs_vnindex"],
                             "abstain": f["abstain"], "strength": f.get("evidence_strength"), "valid": valid,
                             "model_p_up": f.get("model_p_up"), "model_p_outperform": f.get("model_p_outperform")})
    return pd.DataFrame(rows)


def walk_forward_probs(df: pd.DataFrame) -> pd.DataFrame:
    """Per row: calibrated P(up) for abs and rel from the same variant's forecasts labelled before its session, plus
    the base rate of those forecasts (climatology)."""
    out = []
    for (v, h), g in df.groupby(["variant", "horizon"]):
        g = g.sort_values("session")
        for s, cur in g.groupby("session"):
            past = g[(g["exit_date"] <= s) & ~g["abstain"]]
            tab = calibration.table(past)
            for r in cur.itertuples():
                row = {"idx": r.Index}
                for kind, col in (("abs", "absolute_trend"), ("rel", "relative_trend_vs_vnindex")):
                    p, status, _ = calibration.lookup(tab, h, kind, getattr(r, col))
                    base = tab.get((h, kind, "*"), {}).get("p_up")
                    row[f"p_{kind}"], row[f"cal_{kind}"], row[f"base_{kind}"] = p, status, base
                out.append(row)
    return df.join(pd.DataFrame(out).set_index("idx"))


def _brier(p: pd.Series, y: pd.Series) -> float:
    return float(((p - y) ** 2).mean()) if len(p) else np.nan


def _logloss(p: pd.Series, y: pd.Series) -> float:
    p = p.clip(1e-4, 1 - 1e-4)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()) if len(p) else np.nan


def basket(d: pd.DataFrame, col: str, trend: str, ret: str, h: int) -> dict:
    """Equal-weight basket of `trend` names per session (fillable only): mean of per-session mean `ret`, 2·SE."""
    x = d[(d[col] == trend) & d["fillable"]].groupby("session")[ret].mean()
    if len(x) < 2:
        return {"sessions": int(len(x)), "mean": float(x.mean()) if len(x) else None, "two_se": None}
    se = x.std(ddof=1) / np.sqrt(len(x)) * np.sqrt(max(1.0, h / STEP))
    return {"sessions": int(len(x)), "names_per_session": float(d[(d[col] == trend) & d["fillable"]].groupby("session").size().mean()),
            "mean": float(x.mean()), "two_se": float(2 * se)}


def score(d: pd.DataFrame, h: int, kind: str) -> dict:
    from sklearn.metrics import balanced_accuracy_score, f1_score
    col, lab, ret = (("absolute_trend", "absolute_label", "net_return") if kind == "abs" else
                     ("relative_trend_vs_vnindex", "relative_label", "alpha"))
    act = d[~d["abstain"] & d[col].isin(TRENDS)]
    directional = act[act[col] != "neutral"]
    hit = (np.sign(directional[ret]) == directional[col].map({"bullish": 1, "bearish": -1}))
    y = (d[ret] > 0).astype(float)
    p_cal = d[f"p_{kind}"].fillna(d[f"base_{kind}"])
    ok = p_cal.notna() & d[f"base_{kind}"].notna()
    res = {"n": int(len(d)), "abstain_rate": float(d["abstain"].mean()) if len(d) else None,
           "predicted": act[col].value_counts().to_dict(), "actual": act[lab].value_counts().to_dict(),
           "macro_f1": float(f1_score(act[lab], act[col], labels=list(TRENDS), average="macro", zero_division=0)) if len(act) else None,
           "balanced_accuracy": float(balanced_accuracy_score(act[lab], act[col])) if len(act) else None,
           "directional_n": int(len(directional)), "directional_accuracy": float(hit.mean()) if len(directional) else None,
           "share_calibrated": float((d[f"cal_{kind}"] == "calibrated").mean()) if len(d) else None,
           "brier": _brier(p_cal[ok], y[ok]), "brier_climatology": _brier(d.loc[ok, f"base_{kind}"], y[ok]),
           "logloss": _logloss(p_cal[ok], y[ok]),
           "bullish_basket": basket(d, col, "bullish", ret, h), "bearish_basket": basket(d, col, "bearish", ret, h)}
    mp = "model_p_up" if kind == "abs" else "model_p_outperform"
    if d[mp].notna().any():
        m = d[mp].notna()
        res["brier_model_p"] = _brier(d.loc[m, mp].astype(float), y[m])
    return res


def evaluate(variants: list[str]) -> dict:
    from core.db import get_conn
    panel = load_panel()
    sessions = test_sessions(panel)
    labels = labels_for(panel, panel.universe, sessions)
    df = load_forecasts(variants).merge(labels, on=["ticker", "session", "horizon"], how="inner")
    if df.empty:
        raise RuntimeError("no labelled backtest forecasts")
    df = walk_forward_probs(df)
    cut = sorted(df["session"].unique())
    holdout_from = cut[int(len(cut) * (1 - HOLDOUT_SHARE))]
    # compare on the rows every variant has (same tickers, sessions, horizons)
    keys = df.groupby(["ticker", "session", "horizon"])["variant"].nunique()
    common = keys[keys == df["variant"].nunique()].index
    df = df.set_index(["ticker", "session", "horizon"]).loc[common].reset_index()
    metrics = {}
    for (v, h), g in df.groupby(["variant", "horizon"]):
        for period, d in (("all", g), ("holdout", g[g["session"] >= holdout_from])):
            for kind in ("abs", "rel"):
                metrics[f"{v}|{h}|{kind}|{period}"] = score(d, h, kind)
    config = {"variants": variants, "versions": {v: VERSIONS[v] for v in variants}, "feature_version": FEATURE_VERSION,
              "label_version": LABEL_VERSION, "step": STEP, "sessions": [cut[0], cut[-1]], "n_sessions": len(cut),
              "holdout_from": holdout_from, "tickers": len(panel.universe), "rows_per_variant": int(len(common))}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO backtest_runs (config, metrics, calibration) VALUES (%s, %s, %s) RETURNING id",
                    (json.dumps(config), json.dumps(metrics, default=float),
                     json.dumps({v: calibration.to_json(calibration.table(df[(df["variant"] == v) & ~df["abstain"]]))
                                 for v in variants}, default=float)))
        run_id = cur.fetchone()[0]
    return {"id": run_id, "config": config, "metrics": metrics}


def report(res: dict) -> str:
    c, m = res["config"], res["metrics"]
    lines = [f"backtest_runs #{res['id']}: {c['n_sessions']} sessions {c['sessions'][0]}..{c['sessions'][1]}, "
             f"holdout from {c['holdout_from']}, {c['rows_per_variant']} rows per variant", "",
             "| variant | h | kind | period | abstain | macro F1 | bal. acc | dir. acc (n) | Brier cal / clim | "
             "bullish basket % ±2SE | bearish basket % ±2SE |", "|" + "---|" * 11]
    pct = lambda b: "—" if b["mean"] is None else f"{b['mean'] * 100:+.2f}" + ("" if b["two_se"] is None else f" ± {b['two_se'] * 100:.2f}") + f" ({b['sessions']})"  # noqa: E731
    f = lambda x, p=3: "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{p}f}"  # noqa: E731
    for k in sorted(m, key=lambda k: (k.split("|")[1], k.split("|")[2], k.split("|")[3], k.split("|")[0])):
        v, h, kind, period = k.split("|")
        s = m[k]
        lines.append(f"| {v} | {h} | {kind} | {period} | {f(s['abstain_rate'], 2)} | {f(s['macro_f1'])} | "
                     f"{f(s['balanced_accuracy'])} | {f(s['directional_accuracy'])} ({s['directional_n']}) | "
                     f"{f(s['brier'])} / {f(s['brier_climatology'])} | {pct(s['bullish_basket'])} | {pct(s['bearish_basket'])} |")
    return "\n".join(lines)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--variants", default="A,B,C,D")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    vs = a.variants.split(",")
    if a.replay:
        replay(vs, a.workers)
    if a.evaluate:
        print(report(evaluate(vs)))
