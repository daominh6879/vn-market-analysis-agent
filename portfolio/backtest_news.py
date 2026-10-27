"""
portfolio/backtest_news.py — does the news scorer's direction predict the price? (event study)

Every (article, ticker) the scorer judged is stored in news_impact_scores (advice runs, or --backfill here). For each
pair: base = the ticker's close on the last session before the article's (VN) date; entry = the close of the first
session after that date (an advice run can act on the article by then); returns after entry over HORIZONS sessions,
minus the equal-weight liquid universe over the same closes (stock-picking, not the index's few large caps — see
backtest_candidates). `pre` = base -> entry: how much the price had already moved by the time we could act.

Grouped by direction (and score >= 2, kind, magnitude). Pairs are averaged per entry date first, the SE is across
dates, widened by sqrt(h) because daily windows overlap. "pos - neg" is the difference of the two means with
SE = sqrt(se_pos^2 + se_neg^2). A direction that does not separate prices (2·SE band around the difference includes
0) should not be weighted more in the advice.

--backfill scores past ticker-tagged articles of liquid tickers that are not scored yet (same LLM pass as the advice;
title + snippet only, no prices). Caveat: the model's training data may contain some of these events.

Usage:
    python -m portfolio.backtest_news --backfill --since 2026-04-01 --until 2026-10-20 [--workers 4]
    python -m portfolio.backtest_news
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

HORIZONS = (1, 3, 5, 10)
BATCH_PAIRS = 40
VN_TZ = "Asia/Ho_Chi_Minh"


# ── backfill ──────────────────────────────────────────────────────────────────────────────────────────────────────

def backfill(since: str, until: str, workers: int) -> None:
    from core.db import get_conn
    from portfolio.evidence import MIN_AVG_VALUE_BN, _article, save_scores
    from portfolio.news_scorer import score_pairs
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM (SELECT ticker, AVG(close * volume) / 1e6 v FROM ohlcv_daily "
                    "WHERE date >= %s::date - 30 AND date <= %s GROUP BY ticker) x WHERE v >= %s", (since, until, MIN_AVG_VALUE_BN))
        liquid = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT ticker, sector, tags, company_name FROM securities WHERE ticker = ANY(%s)", (liquid,))
        meta = {t: {"sector": s, "tags": tg or [], "company_name": n} for t, s, tg, n in cur.fetchall()}
        cur.execute(
            "SELECT a.id, a.url, a.title, left(a.body, 800), a.source, a.published_at, t.ticker "
            "FROM news_articles a CROSS JOIN LATERAL unnest(a.tickers) t(ticker) "
            "WHERE a.published_at >= %s AND a.published_at < %s::date + 1 AND t.ticker = ANY(%s) "
            "AND NOT EXISTS (SELECT 1 FROM news_impact_scores s WHERE s.article_id = a.id AND s.ticker = t.ticker) "
            "ORDER BY a.published_at", (since, until, liquid))
        rows = cur.fetchall()
    pairs = [(r[6], {**_article(r), "kind": "ticker"}) for r in rows]
    batches = [pairs[i:i + BATCH_PAIRS] for i in range(0, len(pairs), BATCH_PAIRS)]
    print(f"{len(pairs)} unscored pairs, {len(liquid)} liquid tickers, {len(batches)} LLM calls")

    def run(batch):
        todo: dict[str, list[dict]] = {}
        for t, a in batch:
            todo.setdefault(t, []).append(a)
        try:
            scores = score_pairs(todo, meta)
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"[:200]
        with get_conn() as conn:
            save_scores(conn, scores, {k: ("ticker", None) for k in scores}, "backfill")
        return len(scores), None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (n, err) in enumerate(pool.map(run, batches), 1):
            done += n
            if err or i % 10 == 0:
                print(f"  batch {i}/{len(batches)}: {done} stored" + (f" — {err}" if err else ""), flush=True)
    print(f"backfill done: {done}/{len(pairs)} pairs stored")


# ── event study ───────────────────────────────────────────────────────────────────────────────────────────────────

def load_events() -> pd.DataFrame:
    from core.db import get_conn
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT s.article_id, s.ticker, s.kind, s.score, s.direction, s.magnitude, s.origin, a.published_at "
            "FROM news_impact_scores s JOIN news_articles a ON a.id = s.article_id", conn)
    df["pub_date"] = pd.to_datetime(df["published_at"], utc=True).dt.tz_convert(VN_TZ).dt.tz_localize(None).dt.normalize()
    return df


def event_returns(ev: pd.DataFrame, close: pd.DataFrame, liquid: pd.DataFrame) -> pd.DataFrame:
    """Per event: entry date, pre (base -> entry) and excess vs the liquid basket over each horizon (fractions).
    close: wide closes (date x ticker); liquid: same shape, True where the ticker is in the liquid universe."""
    cal = close.index
    basket = {h: (close.shift(-h) / close - 1).where(liquid).mean(axis=1) for h in HORIZONS}
    rows = []
    for e in ev.itertuples(index=False):
        if e.ticker not in close.columns:
            continue
        i_entry = cal.searchsorted(e.pub_date, side="right")             # first session after the article's date
        i_base = cal.searchsorted(e.pub_date, side="left") - 1           # last session before it
        if i_base < 0 or i_entry >= len(cal):
            continue
        c = close[e.ticker]
        row = {"entry": cal[i_entry], "ticker": e.ticker, "kind": e.kind, "score": e.score, "direction": e.direction,
               "magnitude": e.magnitude, "pre": c.iloc[i_entry] / c.iloc[i_base] - 1}
        for h in HORIZONS:
            if i_entry + h < len(cal):
                row[f"x{h}"] = c.iloc[i_entry + h] / c.iloc[i_entry] - 1 - basket[h].iloc[i_entry]
        rows.append(row)
    return pd.DataFrame(rows)


def _mean_se(x: pd.DataFrame, col: str, h: int) -> tuple[float, float, int]:
    per_date = x.dropna(subset=[col]).groupby("entry")[col].mean()
    if len(per_date) < 2:
        return (per_date.mean() if len(per_date) else np.nan), np.nan, len(per_date)
    return per_date.mean(), per_date.std(ddof=1) / np.sqrt(len(per_date)) * np.sqrt(h), len(per_date)


def summarise(r: pd.DataFrame) -> pd.DataFrame:
    """Rows per subset x direction, plus a 'pos - neg' row per subset. Values in %."""
    subsets = {"all": r, "score>=2": r[r["score"] >= 2], "ticker news": r[r["kind"] == "ticker"],
               "sector/related, score>=2": r[(r["kind"] != "ticker") & (r["score"] >= 2)],
               "score>=2, magnitude 3": r[(r["score"] >= 2) & (r["magnitude"] == 3)]}
    out = []
    for name, s in subsets.items():
        stats = {}
        for d, label in ((1, "pos"), (0, "neutral"), (-1, "neg")):
            g = s[s["direction"] == d]
            row = {"subset": name, "direction": label, "pairs": len(g)}
            pre_m, pre_se, _ = _mean_se(g, "pre", 1)
            row["pre"] = pre_m * 100
            for h in HORIZONS:
                m, se, n = _mean_se(g, f"x{h}", h) if f"x{h}" in g else (np.nan, np.nan, 0)
                row[f"x{h}"], row[f"se{h}"], row["dates"] = m * 100, se * 100, max(row.get("dates", 0), n)
                stats[(label, h)] = (m, se)
            out.append(row)
        diff = {"subset": name, "direction": "pos - neg", "pairs": None, "pre": np.nan}
        for h in HORIZONS:
            (mp, sp), (mn, sn) = stats[("pos", h)], stats[("neg", h)]
            diff[f"x{h}"], diff[f"se{h}"] = (mp - mn) * 100, np.sqrt(sp ** 2 + sn ** 2) * 100
        out.append(diff)
    return pd.DataFrame(out)


def run() -> pd.DataFrame:
    from portfolio.backtest_candidates import load
    from portfolio.evidence import MIN_AVG_VALUE_BN
    d = load()
    ev = load_events()
    r = event_returns(ev, d["close"], d["value_bn"] >= MIN_AVG_VALUE_BN)
    print(f"{len(ev)} scored pairs, {len(r)} with prices; entries {r['entry'].min().date()}..{r['entry'].max().date()}"
          if len(r) else f"{len(ev)} scored pairs, none with prices yet")
    return summarise(r) if len(r) else pd.DataFrame()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--since", default="2026-04-01")
    ap.add_argument("--until", default="2026-10-20")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.backfill:
        backfill(a.since, a.until, a.workers)
    else:
        print(run().to_markdown(index=False, floatfmt="+.2f"))
