"""
portfolio/evidence.py — everything the AI judge reads about a portfolio, no LLM.

Per holding and per candidate: technicals, foreign flows, recent news (each article has a stable id so the judge
can cite it). Plus market context and portfolio risk. Fundamentals are not gathered here yet.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from portfolio import ledger

# ── constants ─────────────────────────────────────────────────────────────────
MAX_TICKER_WEIGHT = 0.25         # max share of NAV in one ticker
SECTOR_PREFERRED_MAX = 0.30      # candidates prefer sectors under this NAV share; also the sector risk flag
MIN_AVG_VALUE_BN = 5.0           # liquidity floor for candidates: 20-session average traded value, tỷ đồng
CANDIDATE_COUNT = 5
CANDIDATE_RET20_RANGE = (0.0, 0.25)   # skip falling or already overheated names
NEWS_SNIPPET_CHARS = 400
BETA_SESSIONS = 120
BETA_HIGH = 1.3
CASH_LOW_PCT, CASH_HIGH_PCT = 5.0, 50.0
HIGH_VOL_PCT = 0.90              # 20-session volatility percentile (among liquid stocks) that raises the vol flag
VOL_SESSIONS = 20


def news_days() -> int:
    return int(os.getenv("PORTFOLIO_NEWS_DAYS", "14"))


def news_per_ticker() -> int:
    return int(os.getenv("PORTFOLIO_NEWS_PER_TICKER", "8"))


# ── news ──────────────────────────────────────────────────────────────────────

def group_news(rows: list[tuple], tickers: list[str], per_ticker: int) -> dict[str, list[dict]]:
    """rows = (id, url, title, body, source, published_at, tickers) newest first -> {ticker: [article...]}.
    Every requested ticker gets a key (empty list when there is no news) so the judge is told explicitly."""
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    for id_, url, title, body, source, published_at, tks in rows:
        art = {"id": id_, "url": url, "title": title, "source": source,
               "published_at": published_at.date().isoformat() if hasattr(published_at, "date") else str(published_at)[:10],
               "snippet": " ".join((body or "").split())[:NEWS_SNIPPET_CHARS]}
        for t in tks or []:
            if t in out and len(out[t]) < per_ticker:
                out[t].append(art)
    return out


def fetch_news(tickers: list[str], days: int | None = None, per_ticker: int | None = None) -> dict[str, list[dict]]:
    from core.db import get_conn
    days, per_ticker = days or news_days(), per_ticker or news_per_ticker()
    if not tickers:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, url, title, body, source, published_at, tickers FROM news_articles "
                "WHERE published_at >= NOW() - %s * INTERVAL '1 day' AND tickers && %s "
                "ORDER BY published_at DESC", (days, tickers))
            rows = cur.fetchall()
    return group_news(rows, tickers, per_ticker)


def fetch_market_news(limit: int = 6, days: int | None = None) -> list[dict]:
    """Recent articles not tagged to any ticker = market-wide news."""
    from core.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, url, title, body, source, published_at, tickers FROM news_articles "
                "WHERE published_at >= NOW() - %s * INTERVAL '1 day' AND cardinality(tickers) = 0 "
                "ORDER BY published_at DESC LIMIT %s", (days or news_days(), limit))
            rows = cur.fetchall()
    return group_news([(*r[:6], ["_market"]) for r in rows], ["_market"], limit)["_market"]


# ── prices / flows ────────────────────────────────────────────────────────────

def _avg_values_and_prices(tickers: list[str]) -> dict[str, dict]:
    """20-session average traded value (tỷ đồng) + last price (VND) per ticker."""
    from core.db import get_conn
    if not tickers:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, AVG(close * volume) / 1e6, (ARRAY_AGG(close ORDER BY date DESC))[1] "
                "FROM (SELECT ticker, date, close, volume, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn "
                "      FROM ohlcv_daily WHERE ticker = ANY(%s)) t WHERE rn <= 20 GROUP BY ticker", (tickers,))
            return {t: {"avg_value_bn": float(v), "price": float(p) * 1000} for t, v, p in cur.fetchall()}


def _foreign_net(tickers: list[str]) -> dict[str, dict]:
    """Foreign net value (tỷ đồng) over the last 5 / 20 sessions per ticker."""
    from core.db import get_conn
    if not tickers:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, SUM(net_value) FILTER (WHERE rn <= 5), SUM(net_value) FILTER (WHERE rn <= 20) "
                "FROM (SELECT ticker, net_value, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn "
                "      FROM foreign_flows WHERE ticker = ANY(%s)) t WHERE rn <= 20 GROUP BY ticker", (tickers,))
            return {t: {"net_5d_bn": float(a or 0), "net_20d_bn": float(b or 0)} for t, a, b in cur.fetchall()}


def _betas(tickers: list[str]) -> dict[str, float]:
    """Beta vs VNINDEX over the last BETA_SESSIONS sessions of daily returns."""
    from core.db import get_conn
    if not tickers:
        return {}
    with get_conn() as conn:
        px = pd.read_sql_query(
            "SELECT ticker, date, close FROM ohlcv_daily WHERE ticker = ANY(%(t)s) AND date >= "
            "(SELECT (MAX(date) - %(d)s * INTERVAL '1 day')::date FROM ohlcv_daily)", conn,
            params={"t": tickers, "d": int(BETA_SESSIONS * 1.6)})
        idx = pd.read_sql_query("SELECT date, close FROM market_index_daily WHERE index_code = 'VNINDEX' "
                                "ORDER BY date DESC LIMIT %(n)s", conn, params={"n": BETA_SESSIONS + 1})
    if px.empty or idx.empty:
        return {}
    mkt = idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"].astype(float).sort_index().pct_change()
    wide = px.assign(date=pd.to_datetime(px["date"])).pivot_table(index="date", columns="ticker", values="close").astype(float)
    rets = wide.pct_change(fill_method=None).tail(BETA_SESSIONS)
    out = {}
    for t in rets.columns:
        j = pd.concat([rets[t], mkt], axis=1, join="inner").dropna()
        if len(j) >= 30 and j.iloc[:, 1].var() > 0:
            out[t] = float(j.iloc[:, 0].cov(j.iloc[:, 1]) / j.iloc[:, 1].var())
    return out


def _universe_stats() -> pd.DataFrame:
    """Per liquid active ticker: ret_20, vol_20 (daily-return std), avg_value_bn, last price (VND), sector."""
    from core.db import get_conn
    with get_conn() as conn:
        px = pd.read_sql_query(
            "SELECT ticker, date, close, volume FROM ohlcv_daily WHERE ticker IN (SELECT ticker FROM securities WHERE is_active) "
            "AND date >= (SELECT (MAX(date) - %(d)s * INTERVAL '1 day')::date FROM ohlcv_daily)", conn,
            params={"d": int(VOL_SESSIONS * 2.2)})
        sectors = pd.read_sql_query("SELECT ticker, sector FROM securities WHERE is_active", conn)
    if px.empty:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])
    close = px.pivot_table(index="date", columns="ticker", values="close").astype(float).tail(VOL_SESSIONS + 1)
    value_bn = (px.assign(v=px["close"].astype(float) * px["volume"].astype(float) / 1e6)
                .pivot_table(index="date", columns="ticker", values="v").tail(VOL_SESSIONS).mean())
    ok = close.iloc[1:].notna().sum() >= 15
    df = pd.DataFrame({
        "vol_20": close.pct_change(fill_method=None).iloc[1:].std(),
        "ret_20": close.iloc[-1] / close.iloc[0] - 1,
        "avg_value_bn": value_bn,
        "price": close.ffill().iloc[-1] * 1000,
    })[ok].dropna()
    df["sector"] = sectors.set_index("ticker")["sector"].reindex(df.index).fillna("Unknown")
    return df


def _vol_percentiles(tickers: list[str], universe: pd.DataFrame) -> dict[str, dict]:
    """vol_20 of `tickers` and its percentile among liquid stocks (avg value >= floor)."""
    if universe.empty:
        return {}
    liquid = universe[universe["avg_value_bn"] >= MIN_AVG_VALUE_BN]["vol_20"]
    return {t: {"vol_20": float(universe.at[t, "vol_20"]), "vol_pct": float((liquid < universe.at[t, "vol_20"]).mean())}
            for t in tickers if t in universe.index}


def rank_candidates(universe: pd.DataFrame, flows: dict[str, dict], held: set[str],
                    sector_nav_pct: dict[str, float]) -> list[dict]:
    """Pre-filter new-idea candidates the judge will review: liquid, not held, steady 20-session uptrend (not
    overheated), foreign net buying over 5 sessions. Sectors under the NAV cap first, then by foreign net 20d."""
    if universe.empty:
        return []
    lo, hi = CANDIDATE_RET20_RANGE
    df = universe[(universe["avg_value_bn"] >= MIN_AVG_VALUE_BN) & universe["ret_20"].between(lo, hi)
                  & ~universe.index.isin(held)]
    rows = []
    for t, r in df.iterrows():
        f = flows.get(t)
        if not f or f["net_5d_bn"] <= 0:
            continue
        pct = sector_nav_pct.get(r["sector"], 0.0)
        rows.append({"ticker": t, "sector": r["sector"], "price": float(r["price"]), "avg_value_bn": float(r["avg_value_bn"]),
                     "ret_20": float(r["ret_20"]), "foreign": f, "sector_nav_pct": pct})
    rows.sort(key=lambda c: (c["sector_nav_pct"] >= SECTOR_PREFERRED_MAX, -c["foreign"]["net_20d_bn"]))
    return rows[:CANDIDATE_COUNT]


# ── risk ──────────────────────────────────────────────────────────────────────

def compute_risk(positions: list[dict], cash: dict, betas: dict[str, float],
                 vols: dict[str, dict] | None = None) -> dict:
    """Concentration, sector exposure, cash share and equity beta. Weights are shares of NAV."""
    nav = cash["nav"] or 0.0
    by_sector: dict[str, float] = {}
    for p in positions:
        by_sector[p["sector"]] = by_sector.get(p["sector"], 0.0) + (p["market_value"] or 0.0)
    top = max(positions, key=lambda p: p["market_value"] or 0, default=None)
    sector_top = max(by_sector.items(), key=lambda kv: kv[1], default=(None, 0.0))
    mv = sum(p["market_value"] or 0 for p in positions)
    known = [(p["market_value"] or 0, betas[p["ticker"]]) for p in positions if p["ticker"] in betas]
    beta = (sum(v * b for v, b in known) / sum(v for v, _ in known)) if known and sum(v for v, _ in known) else None
    risk = {
        "nav": nav,
        "cash_pct": cash["cash"] / nav * 100 if nav else 100.0,
        "equity_pct": mv / nav * 100 if nav else 0.0,
        "largest_ticker": top["ticker"] if top else None,
        "largest_ticker_pct": (top["market_value"] or 0) / nav * 100 if top and nav else 0.0,
        "largest_sector": sector_top[0],
        "largest_sector_pct": sector_top[1] / nav * 100 if nav else 0.0,
        "sector_pct": {k: v / nav * 100 for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])} if nav else {},
        "beta": beta,
        "flags": [],
        "high_vol": [{"ticker": p["ticker"], "vol_20": vols[p["ticker"]]["vol_20"], "vol_pct": vols[p["ticker"]]["vol_pct"],
                      "weight_pct": (p["market_value"] or 0) / nav * 100 if nav else 0.0}
                     for p in positions if vols and p["ticker"] in vols and vols[p["ticker"]]["vol_pct"] >= HIGH_VOL_PCT],
    }
    if risk["high_vol"]:
        risk["flags"].append("vol_high")
    if risk["largest_ticker_pct"] > MAX_TICKER_WEIGHT * 100:
        risk["flags"].append("ticker_concentration")
    if risk["largest_sector_pct"] > SECTOR_PREFERRED_MAX * 100:
        risk["flags"].append("sector_concentration")
    if risk["cash_pct"] < CASH_LOW_PCT:
        risk["flags"].append("cash_low")
    if risk["cash_pct"] > CASH_HIGH_PCT:
        risk["flags"].append("cash_high")
    if beta is not None and beta > BETA_HIGH:
        risk["flags"].append("beta_high")
    return risk


# ── entry point ───────────────────────────────────────────────────────────────

def gather_evidence(user_id: str) -> dict:
    """Raises ValueError for an empty portfolio. No LLM call."""
    from agents.intents import closing_commentary, technical

    positions = ledger.get_positions(user_id)
    cash = ledger.get_cash(user_id)
    if not positions and cash["cash"] <= 0:
        raise ValueError(f"portfolio of {user_id} is empty (no holdings, no cash)")
    held = sorted(p["ticker"] for p in positions)

    sector_nav_pct: dict[str, float] = {}
    for p in positions:
        sector_nav_pct[p["sector"]] = sector_nav_pct.get(p["sector"], 0.0) + (p["market_value"] or 0) / cash["nav"]

    universe = _universe_stats()
    flows_all = _foreign_net(sorted(universe.index)) if not universe.empty else {}
    candidates = rank_candidates(universe, flows_all, set(held), sector_nav_pct)
    cand_tickers = [c["ticker"] for c in candidates]
    subjects = held + cand_tickers

    def _tech(t: str) -> str:
        try:
            return technical.gather_data(t, "")
        except Exception as exc:
            return f"[KỸ THUẬT {t}] lỗi: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        market_f = pool.submit(closing_commentary.gather_data, None, "")
        tech_f = {t: pool.submit(_tech, t) for t in subjects}
        try:
            market = market_f.result()
        except Exception as exc:
            market = f"[THỊ TRƯỜNG] không lấy được dữ liệu: {exc}"
        technical_text = {t: f.result() for t, f in tech_f.items()}

    news = fetch_news(subjects)
    flows = {t: flows_all[t] for t in subjects if t in flows_all} or _foreign_net(subjects)
    return {
        "user_id": user_id, "as_of": cash["as_of"], "positions": positions, "cash": cash,
        "candidates": candidates, "market": market, "market_news": fetch_market_news(),
        "technical": technical_text, "news": news, "foreign_flows": flows,
        "risk": compute_risk(positions, cash, _betas(held), _vol_percentiles(held, universe)),
    }
