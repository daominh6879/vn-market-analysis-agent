"""
portfolio/evidence.py — everything the AI judge reads about a portfolio, no LLM.

Per holding and per candidate: technicals, fundamentals, breakout signals, foreign flows, recent news (each article has a stable id so the judge
can cite it). Plus market context and portfolio risk.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd


# ── constants ─────────────────────────────────────────────────────────────────
MAX_TICKER_WEIGHT = 0.25         # max share of NAV in one ticker
SECTOR_PREFERRED_MAX = 0.30      # candidates prefer sectors under this NAV share; also the sector risk flag
MIN_AVG_VALUE_BN = 5.0           # liquidity floor for candidates: 20-session average traded value, tỷ đồng
CANDIDATE_COUNT = 5
WATCHLIST_MAX = 5                # watchlist tickers judged per advice run (before the ranked candidates)
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

def _article(row: tuple) -> dict:
    """(id, url, title, body, source, published_at, ...) -> the article dict the advisor shows and cites."""
    id_, url, title, body, source, published_at = row[:6]
    return {"id": id_, "url": url, "title": title, "source": source,
            "published_at": published_at.date().isoformat() if hasattr(published_at, "date") else str(published_at)[:10],
            "snippet": " ".join((body or "").split())[:NEWS_SNIPPET_CHARS]}


def group_news(rows: list[tuple], tickers: list[str], per_ticker: int) -> dict[str, list[dict]]:
    """rows = (id, url, title, body, source, published_at, tickers) newest first -> {ticker: [article...]}.
    Every requested ticker gets a key (empty list when there is no news) so the judge is told explicitly."""
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    for row in rows:
        art, tks = _article(row), row[6]
        for t in tks or []:
            if t in out and len(out[t]) < per_ticker:
                out[t].append(art)
    return out


def fetch_news(tickers: list[str], days: int | None = None,
               per_ticker: int | None = None) -> tuple[dict[str, dict[str, list[dict]]], dict]:
    """({ticker: {"ticker": [...], "sector": [...], "related": [...]}}, scoring status) over every recent article,
    tagged or not. news_relevance picks the ticker's own news and keyword-matched pools of sector / related-sector
    news; news_scorer has the LLM keep the pool articles that actually move the company (keyword cut on failure)."""
    from core.db import get_conn
    from portfolio.news_relevance import POOL_PER_CHANNEL, POOL_PER_GROUP, select_news
    from portfolio.news_scorer import score_news
    days, per_ticker = days or news_days(), per_ticker or news_per_ticker()
    if not tickers:
        return {}, {"scored": False, "pairs": 0, "kept": None, "error": None}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, sector, tags, name_aliases, company_name FROM securities WHERE ticker = ANY(%s)",
                        (tickers,))
            meta = {t: {"sector": s, "tags": tg or [], "aliases": al or [], "company_name": n}
                    for t, s, tg, al, n in cur.fetchall()}
            cur.execute(
                "SELECT id, url, title, left(body, %s), source, published_at, tickers FROM news_articles "
                "WHERE published_at >= NOW() - %s * INTERVAL '1 day' ORDER BY published_at DESC, id DESC",
                (NEWS_SNIPPET_CHARS * 2, days))
            rows = cur.fetchall()
    articles = [{**_article(r), "tickers": r[6] or []} for r in rows]
    meta = {t: meta.get(t, {}) for t in tickers}
    pools = select_news(articles, meta, per_ticker, POOL_PER_GROUP, POOL_PER_GROUP, POOL_PER_CHANNEL)
    pools = {t: {k: [{f: v for f, v in a.items() if f != "tickers"} for a in arts] for k, arts in g.items()}
             for t, g in pools.items()}
    return score_news(pools, meta)


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


def watch_candidates(universe: pd.DataFrame, flows: dict[str, dict], watchlist: list[str], held: set[str],
                     sector_nav_pct: dict[str, float]) -> list[dict]:
    """The user's watchlist as candidates (watch=True), without rank_candidates' filters: the user picked them.
    Held tickers and tickers missing from the universe (inactive / < 15 sessions of data) are skipped. Pure."""
    rows = []
    for t in dict.fromkeys(str(t).upper() for t in watchlist):
        if t in held or universe.empty or t not in universe.index:
            continue
        r = universe.loc[t]
        rows.append({"ticker": t, "sector": r["sector"], "price": float(r["price"]), "avg_value_bn": float(r["avg_value_bn"]),
                     "ret_20": float(r["ret_20"]), "foreign": flows.get(t), "sector_nav_pct": sector_nav_pct.get(r["sector"], 0.0),
                     "watch": True})
    return rows[:WATCHLIST_MAX]


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


# ── one price per ticker ──────────────────────────────────────────────────────

def _quote(ticker: str) -> tuple[float, str] | None:
    """(last close VND, its session date) from the same DB-first/live-fallback OHLCV source technical.gather_data uses,
    so the price line and the technical block of a ticker show the same price."""
    from tools.price import get_historical_ohlcv
    r = get_historical_ohlcv(ticker, days=5)
    if r.status != "ok" or r.data is None or r.data.empty:
        return None
    last = r.data.iloc[-1]
    return float(last["close"]), str(last["time"])[:10]


def _quotes(tickers: list[str]) -> dict[str, tuple[float, str]]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = dict(zip(tickers, pool.map(_quote, tickers)))
    return {t: q for t, q in got.items() if q}


def reprice(positions: list[dict], cash: dict, prices: dict[str, float]) -> tuple[list[dict], dict]:
    """Positions/cash revalued at `prices` (VND); a ticker without a price keeps the caller's. Pure."""
    out = []
    for p in positions:
        price = prices.get(p["ticker"])
        if price is None:
            out.append(dict(p))
            continue
        value, cost = p["quantity"] * price, p["quantity"] * p["avg_cost"]
        out.append({**p, "last_price": price, "market_value": value, "unrealized_pnl": value - cost,
                    "unrealized_pct": (value / cost - 1) * 100 if cost else None})
    mv = sum(p["market_value"] or 0 for p in out)
    for p in out:
        p["weight"] = (p["market_value"] or 0) / mv if mv else 0.0
    nav = cash["cash"] + mv
    net = cash.get("total_deposited", 0.0) - cash.get("total_withdrawn", 0.0)
    return out, {**cash, "market_value": mv, "nav": nav,
                 "total_return_pct": (nav / net - 1) * 100 if net > 0 else cash.get("total_return_pct")}


# ── entry point ───────────────────────────────────────────────────────────────

def risk_for(positions: list[dict], cash: dict) -> dict:
    """compute_risk with betas and volatility percentiles read from the DB."""
    held = sorted(p["ticker"] for p in positions)
    return compute_risk(positions, cash, _betas(held), _vol_percentiles(held, _universe_stats()))


def gather_evidence_for(positions: list[dict], cash: dict, user_id: str = "external",
                        watchlist: list[str] | None = None) -> dict:
    """Evidence for positions/cash supplied by the caller; watchlist tickers come first among the candidates.
    Holdings and candidates are priced from the same OHLCV source as their technical block (positions/NAV revalued).
    Raises ValueError for an empty portfolio, freshness.StaleDataError when the daily data misses the last finished
    session. No LLM call."""
    from agents.intents import breakout, closing_commentary, fundamentals, technical
    from portfolio.freshness import require_fresh

    if not positions and cash["cash"] <= 0:
        raise ValueError(f"portfolio of {user_id} is empty (no holdings, no cash)")
    data_session = require_fresh()["expected_session"]
    held = sorted(p["ticker"] for p in positions)
    quotes = _quotes(held)
    positions, cash = reprice(positions, cash, {t: q[0] for t, q in quotes.items()})

    sector_nav_pct: dict[str, float] = {}
    for p in positions:
        sector_nav_pct[p["sector"]] = sector_nav_pct.get(p["sector"], 0.0) + (p["market_value"] or 0) / cash["nav"]

    universe = _universe_stats()
    flows_all = _foreign_net(sorted(universe.index)) if not universe.empty else {}
    watch = watch_candidates(universe, flows_all, watchlist or [], set(held), sector_nav_pct)
    ranked = rank_candidates(universe, flows_all, set(held) | {c["ticker"] for c in watch}, sector_nav_pct)
    candidates = watch + ranked[:max(0, CANDIDATE_COUNT - len(watch))]      # watchlist takes ranked slots: same prompt size
    cand_tickers = [c["ticker"] for c in candidates]
    subjects = held + cand_tickers
    quotes.update(_quotes(cand_tickers))
    candidates = [{**c, "price": quotes[c["ticker"]][0]} if c["ticker"] in quotes else c for c in candidates]

    def _safe(gather, label: str, t: str) -> str:
        try:
            return gather(t, "")
        except Exception as exc:
            return f"[{label} {t}] lỗi: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        market_f = pool.submit(closing_commentary.gather_data, None, "")
        tech_f = {t: pool.submit(_safe, technical.gather_data, "KỸ THUẬT", t) for t in subjects}
        fund_f = {t: pool.submit(_safe, fundamentals.gather_data, "CƠ BẢN", t) for t in subjects}
        sig_f = {t: pool.submit(_safe, breakout.gather_data, "BREAKOUT", t) for t in subjects}
        try:
            market = market_f.result()
        except Exception as exc:
            market = f"[THỊ TRƯỜNG] không lấy được dữ liệu: {exc}"
        technical_text = {t: f.result() for t, f in tech_f.items()}
        fundamentals_text = {t: f.result() for t, f in fund_f.items()}
        signals_text = {t: f.result() for t, f in sig_f.items()}

    groups, news_scoring = fetch_news(subjects)
    news = {t: g["ticker"] for t, g in groups.items()}
    context_news = {t: g["sector"] + g["related"] for t, g in groups.items()}
    flows = {t: flows_all[t] for t in subjects if t in flows_all} or _foreign_net(subjects)
    return {
        "user_id": user_id, "as_of": cash["as_of"], "data_session": data_session,
        "price_session": max((q[1] for q in quotes.values()), default=data_session), "positions": positions, "cash": cash,
        "candidates": candidates, "market": market, "market_news": fetch_market_news(),
        "technical": technical_text, "fundamentals": fundamentals_text, "signals": signals_text, "news": news, "context_news": context_news, "news_scoring": news_scoring,
        "foreign_flows": flows,
        "risk": compute_risk(positions, cash, _betas(held), _vol_percentiles(held, universe)),
    }


# ── market stats for target-weight suggestions (vn-portfolio /targets/suggest) ─────────────────────────────────────
STATS_SESSIONS = 60              # daily-return volatility window
MA_SESSIONS = 50
MIN_RETURNS = 40                 # a ticker with fewer daily returns in the window gets no stats


def ticker_stats(close: pd.DataFrame, value_bn: pd.DataFrame) -> dict[str, dict]:
    """close: date x ticker closes (nghìn đồng); value_bn: date x ticker traded value (tỷ đồng). Pure.
    -> {ticker: {price (VND), vol_60 (daily-return std), ma50 (VND), above_ma50, avg_value_bn (20 sessions)}}."""
    rets = close.pct_change(fill_method=None).tail(STATS_SESSIONS)
    out = {}
    for t in close.columns:
        r, c = rets[t].dropna(), close[t].dropna()
        if len(r) < MIN_RETURNS or len(c) < MA_SESSIONS:
            continue
        price, ma = float(c.iloc[-1]) * 1000, float(c.tail(MA_SESSIONS).mean()) * 1000
        v = value_bn[t].dropna().tail(20) if t in value_bn else pd.Series(dtype=float)
        out[t] = {"price": price, "vol_60": float(r.std()), "ma50": ma, "above_ma50": price >= ma,
                  "avg_value_bn": float(v.mean()) if len(v) else None}
    return out


def index_trend(closes: pd.Series) -> dict:
    """VNINDEX close vs its MA50 (closes oldest first). Pure."""
    c = closes.dropna()
    if len(c) < MA_SESSIONS:
        return {"close": float(c.iloc[-1]) if len(c) else None, "ma50": None, "above_ma50": None}
    close, ma = float(c.iloc[-1]), float(c.tail(MA_SESSIONS).mean())
    return {"close": close, "ma50": ma, "above_ma50": close >= ma}


def market_stats(tickers: list[str]) -> dict:
    """{as_of, index: {close, ma50, above_ma50}, tickers: {t: {price, vol_60, ma50, above_ma50, avg_value_bn, sector,
    foreign_net_20d_bn}}} — the numbers vn-portfolio sizes target weights from. Unknown tickers are left out."""
    from core.db import get_conn
    tickers = sorted({t.upper() for t in tickers})
    with get_conn() as conn:
        px = pd.read_sql_query(
            "SELECT ticker, date, close, volume FROM ohlcv_daily WHERE ticker = ANY(%(t)s) AND date >= "
            "(SELECT (MAX(date) - %(d)s * INTERVAL '1 day')::date FROM ohlcv_daily)", conn,
            params={"t": tickers, "d": int(STATS_SESSIONS * 1.7)})
        idx = pd.read_sql_query("SELECT date, close FROM market_index_daily WHERE index_code = 'VNINDEX' "
                                "ORDER BY date DESC LIMIT %(n)s", conn, params={"n": MA_SESSIONS})
        sectors = pd.read_sql_query("SELECT ticker, sector FROM securities WHERE ticker = ANY(%(t)s)", conn,
                                    params={"t": tickers}).set_index("ticker")["sector"]
    stats: dict[str, dict] = {}
    as_of = None
    if not px.empty:
        px["date"] = pd.to_datetime(px["date"])
        close = px.pivot_table(index="date", columns="ticker", values="close").astype(float).sort_index()
        value = (px.assign(v=px["close"].astype(float) * px["volume"].astype(float) / 1e6)
                 .pivot_table(index="date", columns="ticker", values="v").sort_index())
        stats = ticker_stats(close, value)
        as_of = close.index[-1].date().isoformat()
    flows = _foreign_net(sorted(stats))
    for t, s in stats.items():
        s["sector"] = sectors.get(t) or "Unknown"
        s["foreign_net_20d_bn"] = flows.get(t, {}).get("net_20d_bn")
    trend = index_trend(idx.sort_values("date")["close"].astype(float)) if not idx.empty else \
        {"close": None, "ma50": None, "above_ma50": None}
    return {"as_of": as_of, "index": trend, "tickers": stats}
