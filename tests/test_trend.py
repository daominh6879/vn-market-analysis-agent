"""Unit tests for trend/ (Ticker Trend Analyzer): point-in-time features, labels, event clustering, judge output
validation, calibration, rule baseline. Synthetic prices, fake LLM, no DB."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from trend import baselines, calibration, judge
from trend.events import cluster
from trend.features import Panel
from trend.labels import COST, label

TICKERS = ["AAA", "BBB", "CCC"]


def make_frames(n=90, seed=1):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-02", periods=n)
    px = []
    for k, t in enumerate(TICKERS):
        close = 20 * np.exp(np.cumsum(rng.normal(0.001 * (k - 1), 0.015, n)))
        for d, c in zip(days, close):
            px.append({"ticker": t, "date": d, "open": c * 0.995, "close": c, "volume": 1_000_000})
    ic = 1200 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.DataFrame({"date": days, "open": ic * 0.998, "close": ic})
    flows = pd.DataFrame([{"ticker": t, "date": d, "net_value": 1.0} for t in TICKERS for d in days])
    meta = pd.DataFrame({"ticker": TICKERS, "sector": ["Bank", "Bank", "Steel"], "company_name": TICKERS,
                         "in_universe": True})
    return pd.DataFrame(px), idx, flows, meta


def panel(until=None, **kw):
    px, idx, flows, meta = make_frames(**kw)
    return Panel(px, idx, flows, meta, until)


def test_snapshot_from_data_cut_at_the_session_equals_the_one_from_full_history():
    full = panel()
    day = full.cal[70].date().isoformat()
    assert full.snapshot("AAA", day) == panel(until=day).snapshot("AAA", day)


def test_future_prices_do_not_change_a_past_snapshot():
    px, idx, flows, meta = make_frames()
    day = sorted(px["date"].unique())[70]
    base = Panel(px.copy(), idx.copy(), flows.copy(), meta).snapshot("BBB", day)
    shocked = px.copy()
    shocked.loc[shocked["date"] > day, "close"] *= 3
    assert Panel(shocked, idx.copy(), flows.copy(), meta).snapshot("BBB", day) == base


def test_short_history_and_signals():
    p = panel()
    early = p.snapshot("AAA", p.cal[30])
    assert early["quality"]["price_data"] == "short_history"
    late = p.snapshot("AAA", p.cal[-1])
    assert late["quality"]["price_data"] == "complete"
    assert late["signals"]["market_regime"] in ("uptrend", "downtrend", "sideways")
    assert late["quality"]["fundamentals"] == "excluded_not_point_in_time"


def test_label_buys_next_open_sells_close_h_sessions_later_after_costs():
    p = panel()
    i = 60
    lab = label(p, "AAA", i, 5)
    entry, exit_ = p.open["AAA"].iloc[i + 1], p.close["AAA"].iloc[i + 5]
    assert lab["entry_date"] == p.cal[i + 1].date().isoformat()
    assert lab["net_return"] == pytest.approx(exit_ / entry - 1 - COST)
    idx = p.index_close.iloc[i + 5] / p.index_open.iloc[i + 1] - 1
    assert lab["alpha"] == pytest.approx(lab["net_return"] - idx)
    assert label(p, "AAA", len(p.cal) - 3, 5) is None          # window not closed yet


def test_entry_at_the_ceiling_is_not_fillable():
    px, idx, flows, meta = make_frames()
    day = sorted(px["date"].unique())[61]
    px.loc[(px["ticker"] == "AAA") & (px["date"] == day), "open"] *= 1.2
    p = Panel(px, idx, flows, meta)
    assert label(p, "AAA", 60, 5)["fillable"] is False
    assert label(p, "BBB", 60, 5)["fillable"] is True


def art(id_, title, minutes):
    return {"id": id_, "title": title, "published_at": datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)}


def test_reprints_cluster_and_distinct_stories_do_not():
    arts = [art(1, "HPG: Hòa Phát lãi 9 tháng gấp đôi cùng kỳ", 0),
            art(2, "Hòa Phát lãi 9 tháng gấp đôi cùng kỳ", 90),
            art(3, "HPG: Người thân lãnh đạo bán cổ phiếu", 120),
            art(4, "Hòa Phát lãi 9 tháng gấp đôi cùng kỳ", 60 * 24 * 5)]   # same title, 5 days later: new event
    got = [[a["id"] for a in c] for c in cluster(arts)]
    assert got == [[1, 2], [3], [4]]


SNAP = None


def snap():
    global SNAP
    if SNAP is None:
        p = panel()
        SNAP = p.snapshot("AAA", p.cal[-1])
    return SNAP


EVENT = {"event_id": "E7", "available_at": "2026-10-20T08:00:00+00:00", "source_type": "press", "event_type": "earnings_result",
         "facts": ["Lãi quý 3 tăng 30%"], "direction": "positive", "impact_channel": "expected_earnings",
         "impact_horizon": "medium_term", "novelty": "medium", "materiality": "high", "verification_status": "unverified",
         "relevance": "direct"}


def answer(ids=("F.return_20d",), horizons=(5, 20), abstain=False):
    return {"ticker": "AAA", "as_of": "x", "forecasts": [
        {"horizon_trading_days": h, "absolute_trend": "insufficient_evidence" if abstain else "bullish",
         "relative_trend_vs_vnindex": "insufficient_evidence" if abstain else "neutral", "evidence_strength": "moderate",
         "drivers": [] if abstain else [{"evidence_ids": list(ids), "direction": "supports_up", "impact": "medium",
                                         "explanation": "..."}],
         "abstain": abstain, "rationale": "..."} for h in horizons]}


def test_validate_accepts_supplied_ids_and_rejects_unknown_ids_or_missing_horizons():
    inp = judge.build_input(snap(), [EVENT], "D")
    assert judge.validate(answer(("F.return_20d", "E7", "Q.trend_20d", "M.regime")), inp)[0]
    assert "unknown evidence ids" in judge.validate(answer(("F.pe_ratio",)), inp)[1]
    assert "horizons" in judge.validate(answer(horizons=(5,)), inp)[1]
    out, err = judge.validate(answer(abstain=True), inp)
    assert err is None and all(f["abstain"] for f in out["forecasts"])


def test_variant_c_sees_events_only():
    inp = judge.build_input(snap(), [EVENT], "C")
    assert "features" not in inp and "market_context" not in inp
    assert judge.evidence_ids(inp) == {"E7", "DQ.news_data"}


class FakeClient:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def generate(self, messages, **kw):
        self.calls.append(messages)
        return type("R", (), {"text": self.texts.pop(0), "model": "fake"})()


def test_judge_retries_once_with_the_validation_error_then_accepts():
    inp = judge.build_input(snap(), [EVENT], "D")
    client = FakeClient([json.dumps(answer(("F.made_up",))), json.dumps(answer(("E7",)))])
    res = judge.judge(inp, "D", client)
    assert res["valid"] and len(client.calls) == 2
    assert "unknown evidence ids" in client.calls[1][-1].content


def test_judge_stores_invalid_twice_as_abstain():
    inp = judge.build_input(snap(), [], "D")
    res = judge.judge(inp, "D", FakeClient(["not json", "still not"]))
    assert not res["valid"] and all(f["abstain"] for f in res["output"]["forecasts"])


def test_variant_c_without_events_abstains_without_a_call():
    client = FakeClient([])
    res = judge.judge(judge.build_input(snap(), [], "C"), "C", client)
    assert not client.calls and res["output"]["forecasts"][0]["absolute_trend"] == "insufficient_evidence"


def test_calibration_needs_min_samples_per_label():
    n = calibration.MIN_SAMPLES
    hist = pd.DataFrame([{"horizon": 5, "absolute_trend": "bullish", "relative_trend_vs_vnindex": "neutral",
                          "net_return": 0.01 if k % 4 else -0.01, "alpha": 0.0} for k in range(n)]
                        + [{"horizon": 5, "absolute_trend": "bearish", "relative_trend_vs_vnindex": "neutral",
                            "net_return": -0.01, "alpha": 0.0}])
    tab = calibration.table(hist)
    p, status, samples = calibration.lookup(tab, 5, "abs", "bullish")
    ups = sum(1 for k in range(n) if k % 4)
    assert status == "calibrated" and samples == n and p == pytest.approx((ups + 1) / (n + 2))
    assert calibration.lookup(tab, 5, "abs", "bearish")[:2] == (None, "not_calibrated")


def test_rule_baseline_follows_trend_and_market():
    s = {"signals": {"trend_20d": "bullish", "market_regime": "sideways", "relative_strength_20d": "negative"},
         "quality": {"price_data": "complete"}}
    f = baselines.rules(s)["forecasts"][0]
    assert (f["absolute_trend"], f["relative_trend_vs_vnindex"]) == ("bullish", "bearish")
    s["signals"]["market_regime"] = "downtrend"
    assert baselines.rules(s)["forecasts"][0]["absolute_trend"] == "neutral"


def test_session_cutoff_uses_the_close():
    from trend.service import session_cutoff
    assert session_cutoff("2026-10-27T10:00:00+07:00") == "2026-10-26"
    assert session_cutoff("2026-10-27T15:30:00+07:00") == "2026-10-27"
    assert session_cutoff("2026-10-27") == "2026-10-27"
    assert session_cutoff(None) is None
