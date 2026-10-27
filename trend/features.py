"""
trend/features.py — quant engine: market data -> point-in-time evidence for the Trend Judge.

Panel loads EOD prices (ohlcv_daily, thousand VND), VNINDEX, foreign flows (tỷ đồng) and sectors, then computes every
feature as a rolling / trailing series, so the value on session D only uses rows up to D's close
(tests/test_trend.py checks a snapshot from data cut at D equals the one from the full history). snapshot() returns
three levels — market regime, sector regime, ticker — as raw values, a categorical reading (signals) and data-quality
flags. The LLM gets the readings and the numbers, never the raw OHLCV.

Feature IDs (F.*, M.*, S.*) are what the judge cites as evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_VERSION = "f1"
UNIVERSE_INDEX = "VN30"          # securities.index_member the MVP forecasts (plan §10)
MIN_AVG_VALUE_BN = 5.0           # liquid universe for breadth / sector baskets / vol percentile (portfolio.evidence)
PRICE_LIMIT = 0.07               # HOSE daily band; a close-to-close move beyond it is a corporate action, not trading
MIN_HISTORY = 60                 # sessions of closes a ticker needs for return_60d / EMA50
QUALITY_WINDOW = 60

# categorical cut-offs (fractions)
REL_STRONG = 0.02                # relative_return_20d / sector vs market beyond ±2% -> positive / negative
BREADTH_POSITIVE, BREADTH_NEGATIVE = 0.60, 0.40
VOL_RATIO_STRONG, VOL_RATIO_MODERATE = 1.5, 1.0
VOL_PCT_HIGH, VOL_PCT_LOW = 0.80, 0.30
FLOW_SHARE = 0.02                # foreign net 20d beyond ±2% of the 20-session traded value -> buying / selling


def _ema(df: pd.DataFrame | pd.Series, span: int):
    return df.ewm(span=span, adjust=False, min_periods=span).mean()


def _regime(close, ema20, ema50, ret20) -> str:
    if close > ema50 and ema20 > ema50 and ret20 > 0:
        return "uptrend"
    if close < ema50 and ema20 < ema50 and ret20 < 0:
        return "downtrend"
    return "sideways"


def _cut(x: float, hi: float, lo: float, labels=("positive", "neutral", "negative")) -> str:
    if x is None or not np.isfinite(x):
        return "unknown"
    return labels[0] if x > hi else labels[2] if x < lo else labels[1]


class Panel:
    """Wide frames (session x ticker) and every derived series. `until` cuts the data at that session (inclusive)."""

    def __init__(self, px: pd.DataFrame, idx: pd.DataFrame, flows: pd.DataFrame, meta: pd.DataFrame,
                 until: str | None = None):
        for df in (px, idx, flows):
            df["date"] = pd.to_datetime(df["date"])
        if until is not None:
            cut = pd.Timestamp(until)
            px, idx, flows = px[px["date"] <= cut], idx[idx["date"] <= cut], flows[flows["date"] <= cut]
        wide = {c: px.pivot_table(index="date", columns="ticker", values=c, aggfunc="last").astype(float).sort_index()
                for c in ("open", "close", "volume")}
        idx = idx.set_index("date").sort_index().astype(float)
        cal = wide["close"].index.intersection(idx.index)
        self.cal = cal
        self.open, self.close, self.volume = (wide[c].reindex(cal) for c in ("open", "close", "volume"))
        self.index_open, self.index_close = idx["open"].reindex(cal), idx["close"].reindex(cal)
        self.meta = meta.set_index("ticker")
        self.universe = sorted(self.meta.index[self.meta["in_universe"]].intersection(self.close.columns))

        c = self.close
        self.ret = {k: c / c.shift(k) - 1 for k in (1, 5, 20, 60)}
        self.ema20, self.ema50 = _ema(c, 20), _ema(c, 50)
        self.value_bn = (c * self.volume / 1e6).rolling(20, min_periods=15).mean()
        self.liquid = self.value_bn >= MIN_AVG_VALUE_BN
        self.vol20 = self.ret[1].rolling(20, min_periods=15).std()
        self.vol_pct = self.vol20.where(self.liquid).rank(axis=1, pct=True)
        self.volume_ratio = self.volume / self.volume.shift(1).rolling(20, min_periods=15).mean()
        f = flows.pivot_table(index="date", columns="ticker", values="net_value", aggfunc="sum").reindex(cal).fillna(0.0)
        self.f5, self.f20 = f.rolling(5).sum(), f.rolling(20).sum()
        self.big_move = (self.ret[1].abs() > PRICE_LIMIT + 0.005).astype(float).rolling(QUALITY_WINDOW, min_periods=1).max()
        self.missing = self.close.isna().astype(float).rolling(QUALITY_WINDOW, min_periods=1).sum()
        self.history = self.close.notna().cumsum()

        ic = self.index_close
        self.index_ret = {k: ic / ic.shift(k) - 1 for k in (5, 20, 60)}
        self.index_ema20, self.index_ema50 = _ema(ic, 20), _ema(ic, 50)
        above = (c > self.ema20).where(self.liquid & self.ema20.notna())
        self.breadth_above_ema20 = above.mean(axis=1)
        self.breadth_adv_5d = (self.ret[5] > 0).where(self.liquid & self.ret[5].notna()).mean(axis=1)
        sectors = self.meta["sector"].reindex(c.columns).fillna("Unknown")
        r20 = self.ret[20].where(self.liquid)
        self.sector_ret20 = {s: r20[cols].mean(axis=1) for s, cols in c.columns.groupby(sectors).items()}
        self.sector_n = {s: r20[cols].notna().sum(axis=1) for s, cols in c.columns.groupby(sectors).items()}
        self.sectors = sectors

    def pos(self, session) -> int:
        """Index of `session` in the calendar (the last session on or before it)."""
        i = self.cal.searchsorted(pd.Timestamp(session), side="right") - 1
        if i < 0:
            raise KeyError(f"no session on or before {session}")
        return i

    def market(self, i: int) -> dict:
        ic, e20, e50, r20 = (float(s.iloc[i]) for s in (self.index_close, self.index_ema20, self.index_ema50,
                                                       self.index_ret[20]))
        b = float(self.breadth_above_ema20.iloc[i])
        return {
            "features": {"M.vnindex_return_5d": float(self.index_ret[5].iloc[i]), "M.vnindex_return_20d": r20,
                         "M.vnindex_close_vs_ema20": ic / e20 - 1, "M.vnindex_close_vs_ema50": ic / e50 - 1,
                         "M.breadth_above_ema20": b, "M.breadth_adv_5d": float(self.breadth_adv_5d.iloc[i])},
            "signals": {"regime": _regime(ic, e20, e50, r20),
                        "breadth": _cut(b, BREADTH_POSITIVE, BREADTH_NEGATIVE, ("positive", "mixed", "negative"))},
        }

    def snapshot(self, ticker: str, session) -> dict:
        """{ticker, session, feature_version, features, signals, quality} for `ticker` on the session <= `session`."""
        i = self.pos(session)
        g = lambda df: float(df[ticker].iloc[i]) if ticker in df.columns else float("nan")  # noqa: E731
        close, e20, e50 = g(self.close), g(self.ema20), g(self.ema50)
        r5, r20, r60 = g(self.ret[5]), g(self.ret[20]), g(self.ret[60])
        sector = self.sectors.get(ticker, "Unknown")
        sec_r20 = self.sector_ret20.get(sector, pd.Series(np.nan, index=self.cal)).iloc[i]
        sec_n = int(self.sector_n.get(sector, pd.Series(0, index=self.cal)).iloc[i])
        ir20 = float(self.index_ret[20].iloc[i])
        value = g(self.value_bn)
        feats = {
            "F.close": close, "F.return_5d": r5, "F.return_20d": r20, "F.return_60d": r60,
            "F.relative_return_5d": r5 - float(self.index_ret[5].iloc[i]), "F.relative_return_20d": r20 - ir20,
            "F.close_vs_ema20": close / e20 - 1, "F.close_vs_ema50": close / e50 - 1,
            "F.volume_ratio_20d": g(self.volume_ratio), "F.volatility_20d": g(self.vol20),
            "F.volatility_pct": g(self.vol_pct), "F.avg_value_20d_bn": value,
            "F.foreign_net_5d_bn": g(self.f5), "F.foreign_net_20d_bn": g(self.f20),
            "S.sector": sector, "S.sector_members": sec_n,
            "S.sector_return_20d": float(sec_r20), "S.sector_vs_market_20d": float(sec_r20) - ir20,
            "S.ticker_vs_sector_20d": r20 - float(sec_r20),
        }
        mk = self.market(i)
        feats.update(mk["features"])
        trend = ("bullish" if close > e20 > e50 and r20 > 0 else "bearish" if close < e20 < e50 and r20 < 0
                 else "neutral") if np.isfinite(e50) else "unknown"
        vr = feats["F.volume_ratio_20d"]
        flow_share = feats["F.foreign_net_20d_bn"] / (value * 20) if value and np.isfinite(value) else np.nan
        signals = {
            "market_regime": mk["signals"]["regime"], "market_breadth": mk["signals"]["breadth"],
            "sector_relative_strength": _cut(feats["S.sector_vs_market_20d"], REL_STRONG, -REL_STRONG,
                                             ("strong", "neutral", "weak")) if sec_n >= 2 else "unknown",
            "trend_20d": trend,
            "relative_strength_20d": _cut(feats["F.relative_return_20d"], REL_STRONG, -REL_STRONG),
            "volume_confirmation": ("unknown" if not np.isfinite(vr) else "strong" if vr >= VOL_RATIO_STRONG
                                    else "moderate" if vr >= VOL_RATIO_MODERATE else "weak"),
            "volatility": _cut(feats["F.volatility_pct"], VOL_PCT_HIGH, VOL_PCT_LOW, ("high", "medium", "low")),
            "foreign_flow": _cut(flow_share, FLOW_SHARE, -FLOW_SHARE, ("buying", "neutral", "selling")),
        }
        history = int(g(self.history)) if np.isfinite(g(self.history)) else 0
        quality = {
            "price_data": "complete" if history >= MIN_HISTORY and g(self.missing) == 0 else
                          "short_history" if history < MIN_HISTORY else "gaps",
            "missing_sessions_60": int(g(self.missing)) if np.isfinite(g(self.missing)) else QUALITY_WINDOW,
            "low_liquidity": bool(not np.isfinite(value) or value < MIN_AVG_VALUE_BN),
            "possible_corporate_action": bool(g(self.big_move) > 0),
            "fundamentals": "excluded_not_point_in_time",
            "stale": bool(np.isnan(close)),
        }
        return {"ticker": ticker, "session": self.cal[i].date().isoformat(), "feature_version": FEATURE_VERSION,
                "features": {k: (round(v, 6) if isinstance(v, float) and np.isfinite(v) else
                                 None if isinstance(v, float) else v) for k, v in feats.items()},
                "signals": signals, "quality": quality}


def load_panel(until: str | None = None) -> Panel:
    from core.db import get_conn
    with get_conn() as conn:
        px = pd.read_sql_query("SELECT ticker, date, open, close, volume FROM ohlcv_daily "
                               "WHERE ticker IN (SELECT ticker FROM securities WHERE is_active)", conn)
        idx = pd.read_sql_query("SELECT date, open, close FROM market_index_daily WHERE index_code = 'VNINDEX'", conn)
        flows = pd.read_sql_query("SELECT ticker, date, net_value FROM foreign_flows", conn)
        meta = pd.read_sql_query("SELECT ticker, sector, company_name, %s = ANY(index_member) AS in_universe "
                                 "FROM securities WHERE is_active", conn, params=(UNIVERSE_INDEX,))
    return Panel(px, idx, flows, meta, until)


def save_snapshots(conn, snaps: list[dict]) -> None:
    import json
    with conn.cursor() as cur:
        for s in snaps:
            cur.execute(
                "INSERT INTO daily_features (ticker, session, feature_version, features, signals, quality) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, session, feature_version) DO UPDATE SET "
                "features = EXCLUDED.features, signals = EXCLUDED.signals, quality = EXCLUDED.quality, computed_at = NOW()",
                (s["ticker"], s["session"], s["feature_version"], json.dumps(s["features"]), json.dumps(s["signals"]),
                 json.dumps(s["quality"])))
