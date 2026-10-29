"""
portfolio/news_facts.py — news, disclosures, LLM impact scores and insider trades for vn-portfolio's rule engine
(/advisor/news-facts), and the scheduled scoring that keeps every ticker-tagged article judged.

Read-only dump, like market_data.py: vn-portfolio classifies titles (its YAML keyword lists), applies the 15:00
disclosure cutoff (khung 2.3-F) and decides; this side only stores and serves. Each (article, ticker) is scored once
by news_scorer (first judgement wins, see evidence.save_scores), from the title and snippet only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

SNIPPET_CHARS = 300
SCORE_ORIGIN = "scheduled"


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def news_facts(since: date | None = None, tickers: list[str] | None = None) -> dict:
    """{as_of, articles, scores, insider_trades, insider_deals}. articles: every article from `since` (all tickers when `tickers` is
    None), with its tagged tickers; scores: the stored judgements of those articles; insider_trades: DDIND / DDINS /
    DDRP notices from `since` with the full source record."""
    from core.db import get_conn

    p = {"since": since or date(1900, 1, 1), "tickers": tickers}
    tick_filter = " AND tickers && %(tickers)s" if tickers else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, published_at, source, title, url, tickers, left(body, %(n)s) FROM news_articles "
                    f"WHERE published_at >= %(since)s{tick_filter} ORDER BY published_at", {**p, "n": SNIPPET_CHARS})
        arts = cur.fetchall()
        ids = [a[0] for a in arts]
        cur.execute("SELECT article_id, ticker, kind, channel, score, direction, magnitude, origin, scored_at "
                    "FROM news_impact_scores WHERE article_id = ANY(%s)", (ids,))
        scores = cur.fetchall()
        cur.execute("SELECT ticker, event_code, title, public_date, start_date, end_date, raw, fetched_at "
                    "FROM insider_trades WHERE (public_date IS NULL OR public_date >= %(since)s)"
                    + (" AND ticker = ANY(%(tickers)s)" if tickers else "") + " ORDER BY public_date", p)
        insider = cur.fetchall()
        cur.execute("SELECT ticker, holder_code, person, position, related_person, related_position, volume_before, "
                    "volume_after, ownership_pct, plan_buy, plan_sell, plan_begin, plan_end, real_buy, real_sell, "
                    "real_end, published_at, note, first_seen_at FROM insider_deals "
                    "WHERE (published_at IS NULL OR published_at >= %(since)s)"
                    + (" AND ticker = ANY(%(tickers)s)" if tickers else "") + " ORDER BY published_at", p)
        dcols = [d[0] for d in cur.description]
        deals = [dict(zip(dcols, r)) for r in cur.fetchall()]
        cur.execute("SELECT max(published_at) FROM news_articles")
        latest = cur.fetchone()[0]
    return {
        "as_of": _iso(latest),
        "articles": [{"id": i, "published_at": _iso(pub), "source": src, "title": t, "url": u, "tickers": tk or [],
                      "snippet": " ".join((body or "").split())} for i, pub, src, t, u, tk, body in arts],
        "scores": [{"article_id": a, "ticker": t, "kind": k, "channel": ch, "score": s, "direction": d,
                    "magnitude": m, "origin": o, "scored_at": _iso(at)} for a, t, k, ch, s, d, m, o, at in scores],
        "insider_trades": [{"ticker": t, "event_code": c, "title": ti, "public_date": _iso(pd_), "start_date": _iso(sd),
                            "end_date": _iso(ed), "raw": raw, "fetched_at": _iso(fa)}
                           for t, c, ti, pd_, sd, ed, raw, fa in insider],
        "insider_deals": [{k: (_iso(v) if hasattr(v, "isoformat") else float(v) if k == "ownership_pct" and v is not None
                               else v) for k, v in d.items()} for d in deals],
    }


def unscored_pairs(since: date, until: date, tickers: list[str] | None = None) -> tuple[list[tuple[str, dict]], dict]:
    """(ticker, article) pairs of ticker-tagged articles in [since, until] not judged yet, and the tickers' profiles
    (news_scorer's `meta`). All active tickers when `tickers` is None."""
    from core.db import get_conn
    from portfolio.evidence import _article
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, sector, tags, company_name FROM securities WHERE is_active"
                    + (" AND ticker = ANY(%s)" if tickers else ""), (tickers,) if tickers else ())
        meta = {t: {"sector": s, "tags": tg or [], "company_name": n} for t, s, tg, n in cur.fetchall()}
        cur.execute(
            "SELECT a.id, a.url, a.title, left(a.body, 800), a.source, a.published_at, t.ticker "
            "FROM news_articles a CROSS JOIN LATERAL unnest(a.tickers) t(ticker) "
            "WHERE a.published_at >= %s AND a.published_at < %s::date + 1 AND t.ticker = ANY(%s) "
            "AND NOT EXISTS (SELECT 1 FROM news_impact_scores s WHERE s.article_id = a.id AND s.ticker = t.ticker) "
            "ORDER BY a.published_at", (since, until, sorted(meta)))
        rows = cur.fetchall()
    return [(r[6], {**_article(r), "kind": "ticker"}) for r in rows], meta


def score_unscored(since: date, until: date | None = None, tickers: list[str] | None = None, workers: int = 4,
                   origin: str = SCORE_ORIGIN) -> dict:
    """Judge every unscored (article, ticker) pair in [since, until] with the same LLM pass as the advice and store it.
    Raises when there was work and fewer than half the pairs came back (so the Dagster run fails and retries)."""
    from core.db import get_conn
    from portfolio.evidence import save_scores
    from portfolio.news_scorer import BATCH_PAIRS, score_pairs
    pairs, meta = unscored_pairs(since, until or date.today(), tickers)
    batches = [pairs[i:i + BATCH_PAIRS] for i in range(0, len(pairs), BATCH_PAIRS)]
    errors: list[str] = []

    def run(batch) -> int:
        todo: dict[str, list[dict]] = {}
        for t, a in batch:
            todo.setdefault(t, []).append(a)
        try:
            scores = score_pairs(todo, meta)
        except Exception as exc:                      # one failed batch must not lose the others
            errors.append(f"{type(exc).__name__}: {exc}"[:200])
            return 0
        with get_conn() as conn:
            save_scores(conn, scores, {k: ("ticker", None) for k in scores}, origin)
        return len(scores)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        stored = sum(pool.map(run, batches))
    out = {"pairs": len(pairs), "stored": stored, "batches": len(batches), "errors": errors[:3]}
    if pairs and stored < len(pairs) / 2:
        raise RuntimeError(f"news scoring: stored {stored}/{len(pairs)} pairs; {errors[:1]}")
    return out


def recent_window(days: int) -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=days), today
