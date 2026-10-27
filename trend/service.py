"""
trend/service.py — live forecasts (plan §8.2): what /api/v1/forecasts and the daily Dagster asset call.

forecast(tickers, as_of) forecasts on the last session on or before as_of (default: the latest stored session) with
variant D, from data cut at that session — a past as_of gets the point-in-time view, never later prices or news.
A (ticker, session) already judged live is served from forecast_runs (the judgement made then, not a new one).
Calibrated probabilities come from every labelled D forecast (backtest replay + live) whose window had closed by the
session; a cell with fewer than calibration.MIN_SAMPLES forecasts is null with status 'not_calibrated' — the LLM is
never asked for a probability.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trend import calibration, judge
from trend.events import load_events
from trend.features import FEATURE_VERSION, UNIVERSE_INDEX, load_panel, save_snapshots
from trend.labels import HORIZONS, LABEL_VERSION, labels_for, save_labels

VARIANT = "D"
CONFIDENCE = {"strong": "high", "moderate": "medium", "weak": "low"}


def _stored_run(cur, ticker: str, session: str) -> dict | None:
    cur.execute("SELECT id, input, output, valid, model, created_at FROM forecast_runs WHERE ticker = %s AND "
                "session = %s AND variant = %s AND mode = 'live' AND prompt_version = %s",
                (ticker, session, VARIANT, judge.PROMPT_VERSION))
    r = cur.fetchone()
    return r and {"id": r[0], "input": r[1], "output": r[2], "valid": r[3], "model": r[4], "created_at": r[5]}


def calibration_table(cur, session: str) -> dict:
    """From every labelled D forecast whose exit session is on or before `session`."""
    cur.execute(
        "SELECT r.output, r.session::text, l.horizon, l.net_return, l.alpha FROM forecast_runs r JOIN forecast_labels l "
        "ON l.ticker = r.ticker AND l.session = r.session AND l.label_version = %s "
        "WHERE r.variant = %s AND r.prompt_version = %s AND l.exit_date <= %s",
        (LABEL_VERSION, VARIANT, judge.PROMPT_VERSION, session))
    rows = []
    for out, _, h, net, alpha in cur.fetchall():
        f = next((f for f in out["forecasts"] if f["horizon_trading_days"] == h), None)
        if f and not f["abstain"]:
            rows.append({"horizon": h, "absolute_trend": f["absolute_trend"],
                         "relative_trend_vs_vnindex": f["relative_trend_vs_vnindex"],
                         "net_return": float(net), "alpha": float(alpha)})
    return calibration.table(pd.DataFrame(rows)) if rows else {}


def present(ticker: str, session: str, run: dict, tab: dict, in_universe: bool) -> dict:
    """forecast_runs row -> API response item."""
    out, inp = run["output"], run["input"]
    items = []
    for f in out["forecasts"]:
        h = f["horizon_trading_days"]
        p_up, st_up, n_up = calibration.lookup(tab, h, "abs", f["absolute_trend"])
        p_out, st_out, n_out = calibration.lookup(tab, h, "rel", f["relative_trend_vs_vnindex"])
        items.append({
            "horizon": h, "absolute_trend": f["absolute_trend"],
            "relative_trend_vs_vnindex": f["relative_trend_vs_vnindex"],
            "calibrated_probability_up": None if f["abstain"] else p_up,
            "calibrated_probability_outperform": None if f["abstain"] else p_out,
            "calibration_status": "not_calibrated" if f["abstain"] else
                                  "calibrated" if st_up == st_out == "calibrated" else
                                  "partially_calibrated" if "calibrated" in (st_up, st_out) else "not_calibrated",
            "calibration_samples": {"absolute": n_up, "relative": n_out},
            "confidence": CONFIDENCE.get(f.get("evidence_strength"), "low"),
            "top_drivers": f.get("drivers", []), "key_risks": f.get("risks", []),
            "conflicting_signals": f.get("conflicting_signals", []), "catalysts": f.get("catalysts", []),
            "invalidation_conditions": f.get("invalidation_conditions", []),
            "rationale": f.get("rationale", ""), "abstain": f["abstain"]})
    return {"ticker": ticker, "as_of": inp["as_of"], "session": session, "forecasts": items,
            "evidence": {k: inp.get(k) for k in ("market_context", "sector_context", "quant_signals", "features",
                                                  "data_quality", "events")},
            "data_quality_note": out.get("data_quality", ""),
            "validated_universe": in_universe,
            "versions": {"feature": inp.get("feature_version", FEATURE_VERSION), "prompt": judge.PROMPT_VERSION,
                         "label": LABEL_VERSION, "model": run.get("model")},
            "run_id": run.get("id"), "valid": run["valid"], "created_at": str(run.get("created_at") or "")}


def forecast(tickers: list[str], as_of: str | None = None, log=print) -> list[dict]:
    from core.db import get_conn
    panel = load_panel(until=session_cutoff(as_of))
    session = panel.cal[-1].date().isoformat()
    out = []
    with get_conn() as conn, conn.cursor() as cur:
        tab = calibration_table(cur, session)
        for t in tickers:
            t = t.upper()
            if t not in panel.close.columns:
                out.append({"ticker": t, "session": session, "error": "unknown ticker or no price data"})
                continue
            run = _stored_run(cur, t, session)
            if run is None:
                snap = panel.snapshot(t, session)
                inp = {**judge.build_input(snap, load_events(cur, t, judge.as_of_for(session)), VARIANT),
                       "feature_version": FEATURE_VERSION}
                res = judge.judge(inp, VARIANT)
                save_snapshots(conn, [snap])
                rid = judge.save_run(conn, t, session, VARIANT, "live", inp, res, feature_version=FEATURE_VERSION)
                conn.commit()
                run = {"id": rid, "input": inp, "output": res["output"], "valid": res["valid"], "model": res["model"]}
                log(f"trend: {t} {session} judged (valid={res['valid']})")
            out.append(present(t, session, run, tab, t in panel.universe))
    return out


def latest(log=print) -> dict:
    """Stored live forecasts of the latest session that has any (no LLM call): the dashboard board."""
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session)::text FROM forecast_runs WHERE mode = 'live' AND variant = %s AND "
                    "prompt_version = %s", (VARIANT, judge.PROMPT_VERSION))
        session = cur.fetchone()[0]
        if not session:
            return {"session": None, "items": []}
        cur.execute("SELECT s.ticker, %s = ANY(s.index_member) FROM securities s", (UNIVERSE_INDEX,))
        universe = {t for t, m in cur.fetchall() if m}
        tab = calibration_table(cur, session)
        cur.execute("SELECT id, ticker, input, output, valid, model, created_at FROM forecast_runs WHERE mode = 'live' "
                    "AND variant = %s AND prompt_version = %s AND session = %s ORDER BY ticker",
                    (VARIANT, judge.PROMPT_VERSION, session))
        rows = cur.fetchall()
    return {"session": session, "items": [
        present(t, session, {"id": i, "input": inp, "output": o, "valid": v, "model": m, "created_at": c}, tab, t in universe)
        for i, t, inp, o, v, m, c in rows]}


def label_live_runs(log=print) -> int:
    """Labels for every forecast session whose window has closed (live and backtest), so calibration and the track
    record keep up."""
    from core.db import get_conn
    panel = load_panel()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker, session FROM forecast_runs")
        pairs = cur.fetchall()
    pos = {d.date(): i for i, d in enumerate(panel.cal)}
    by_session: dict[int, list[str]] = {}
    for t, s in pairs:
        if s in pos:
            by_session.setdefault(pos[s], []).append(t)
    frames = [labels_for(panel, ts, [i]) for i, ts in by_session.items()]
    df = pd.concat([f for f in frames if not f.empty]) if frames else pd.DataFrame()
    if df.empty:
        return 0
    with get_conn() as conn:
        save_labels(conn, df)
    log(f"trend: {len(df)} labels upserted (closed windows)")
    return len(df)


def history(ticker: str, limit: int = 60) -> list[dict]:
    """Track record: past live D forecasts of `ticker` with their realised labels (when the window has closed)."""
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT r.session::text, r.mode, r.output, l.horizon, l.net_return, l.alpha, l.absolute_label, "
            "l.relative_label, l.exit_date::text FROM forecast_runs r LEFT JOIN forecast_labels l ON l.ticker = r.ticker "
            "AND l.session = r.session AND l.label_version = %s WHERE r.ticker = %s AND r.variant = %s "
            "AND r.prompt_version = %s ORDER BY r.session DESC, r.mode LIMIT %s",
            (LABEL_VERSION, ticker.upper(), VARIANT, judge.PROMPT_VERSION, limit * len(HORIZONS)))
        rows = cur.fetchall()
    out: dict[tuple[str, str], dict] = {}
    for s, mode, o, h, net, alpha, al, rl, exit_ in rows:
        item = out.setdefault((s, mode), {"session": s, "mode": mode, "forecasts": {
            f["horizon_trading_days"]: {"absolute_trend": f["absolute_trend"],
                                        "relative_trend_vs_vnindex": f["relative_trend_vs_vnindex"],
                                        "abstain": f["abstain"], "outcome": None} for f in o["forecasts"]}})
        if h in item["forecasts"]:
            item["forecasts"][h]["outcome"] = {"net_return": float(net), "alpha": float(alpha), "absolute_label": al,
                                               "relative_label": rl, "exit_date": exit_}
    return [{**v, "forecasts": [{"horizon": h, **f} for h, f in sorted(v["forecasts"].items())]}
            for v in list(out.values())[:limit]]


def session_cutoff(as_of: str | None) -> str | None:
    """ISO timestamp / date -> the last calendar date whose session data was complete at that moment (a session
    completes at judge.AS_OF_TIME, VN time). None = latest."""
    if not as_of:
        return None
    ts = datetime.fromisoformat(as_of)
    if ts.tzinfo is None and len(as_of) <= 10:
        return ts.date().isoformat()
    local = ts.astimezone(judge.VN_TZ) if ts.tzinfo else ts.replace(tzinfo=judge.VN_TZ)
    d = local.date() if local.time() >= judge.AS_OF_TIME else local.date() - timedelta(days=1)
    return d.isoformat()
