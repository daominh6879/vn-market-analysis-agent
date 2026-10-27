"""
trend/baselines.py — the non-LLM forecasters the Trend Judge must beat (plan §7.3).

A (rules-v1): momentum / MA / volume rules on the same categorical signals the judge sees.
  absolute: bullish when trend_20d is bullish and the market is not in a downtrend, bearish when trend_20d is bearish
  and the market is not in an uptrend, else neutral; relative: the sign of relative_strength_20d.
B (logit-v1): multinomial logistic regression on the numeric features, refit at every test session on the samples
  whose label was complete by then (walk-forward, no future rows). Also gives P(up) from a binary fit.
Both return the judge's output shape so the backtest scores every variant the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trend.labels import HORIZONS

RULES_VERSION = "rules-v1"
LOGIT_VERSION = "logit-v1"
NUMERIC = ["F.return_5d", "F.return_20d", "F.return_60d", "F.relative_return_5d", "F.relative_return_20d",
           "F.close_vs_ema20", "F.close_vs_ema50", "F.volume_ratio_20d", "F.volatility_20d", "F.volatility_pct",
           "S.sector_vs_market_20d", "S.ticker_vs_sector_20d", "M.vnindex_return_20d", "M.vnindex_close_vs_ema50",
           "M.breadth_above_ema20", "M.breadth_adv_5d"]
MIN_TRAIN = 150                  # samples before B forecasts anything (else abstain)


def _forecast(h: int, absolute: str, relative: str, strength: str = "moderate", p_up=None, p_out=None) -> dict:
    abstain = absolute == "insufficient_evidence"
    return {"horizon_trading_days": h, "absolute_trend": absolute, "relative_trend_vs_vnindex": relative,
            "evidence_strength": strength, "drivers": [], "abstain": abstain, "rationale": "",
            **({"model_p_up": p_up, "model_p_outperform": p_out} if p_up is not None else {})}


def rules(snap: dict, horizons=HORIZONS) -> dict:
    s = snap["signals"]
    if snap["quality"]["price_data"] == "short_history" or s["trend_20d"] == "unknown":
        return {"forecasts": [_forecast(h, "insufficient_evidence", "insufficient_evidence", "weak") for h in horizons]}
    a = ("bullish" if s["trend_20d"] == "bullish" and s["market_regime"] != "downtrend" else
         "bearish" if s["trend_20d"] == "bearish" and s["market_regime"] != "uptrend" else "neutral")
    r = {"positive": "bullish", "negative": "bearish"}.get(s["relative_strength_20d"], "neutral")
    return {"forecasts": [_forecast(h, a, r) for h in horizons]}


def vector(snap: dict) -> list[float]:
    return [snap["features"].get(k) if snap["features"].get(k) is not None else np.nan for k in NUMERIC]


class Logit:
    """Walk-forward logistic baseline. fit() on a frame of past samples (X columns NUMERIC, label columns)."""

    def __init__(self):
        self.models: dict = {}

    def fit(self, train: pd.DataFrame) -> "Logit":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        self.models = {}
        if train.empty:
            return self
        for h in HORIZONS:
            d = train[train["horizon"] == h].dropna(subset=NUMERIC)
            if len(d) < MIN_TRAIN:
                continue
            X = d[NUMERIC].to_numpy()
            for target, col in (("abs", "absolute_label"), ("rel", "relative_label")):
                if d[col].nunique() >= 2:
                    self.models[(h, target)] = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500)).fit(X, d[col])
            for target, col in (("p_up", "net_return"), ("p_out", "alpha")):
                y = (d[col] > 0).astype(int)
                if y.nunique() == 2:
                    self.models[(h, target)] = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500)).fit(X, y)
        return self

    def predict(self, snap: dict, horizons=HORIZONS) -> dict:
        x = np.array([vector(snap)])
        out = []
        for h in horizons:
            if (h, "abs") not in self.models or (h, "rel") not in self.models or np.isnan(x).any():
                out.append(_forecast(h, "insufficient_evidence", "insufficient_evidence", "weak"))
                continue
            a, r = (self.models[(h, k)].predict(x)[0] for k in ("abs", "rel"))
            p = {k: float(self.models[(h, k)].predict_proba(x)[0, 1]) if (h, k) in self.models else None
                 for k in ("p_up", "p_out")}
            out.append(_forecast(h, a, r, p_up=p["p_up"], p_out=p["p_out"]))
        return {"forecasts": out}
