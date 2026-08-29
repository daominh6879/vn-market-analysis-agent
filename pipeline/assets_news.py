"""
pipeline/assets_news.py — Dagster assets for news pipeline.

Assets:
  news_raw          — scrape RSS CafeF + VnExpress → upsert news_articles (every 6h)
  fireant_news      — FireAnt API type=1 posts per TICKERS → upsert news_articles (every 6h)
  cafef_ticker_news — CafeF search per TICKERS → upsert news_articles (every 6h)
  news_indexed      — embed unindexed articles → upsert news_chunks (after news_raw)
  news_purge        — delete articles + Qdrant points older than 90 days (weekly)
"""
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, RetryPolicy, asset

EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

from core.tickers import get_tickers as _get_tickers
TICKERS = _get_tickers()


@asset(
    group_name="news",
    description="Scrape CafeF + VnExpress RSS → upsert news_articles. Idempotent via URL unique key.",
    retry_policy=RetryPolicy(max_retries=3, delay=60),
)
def news_raw(context: AssetExecutionContext) -> dict:
    from data.news_scraper import run_scrape, RSS_SOURCES

    stats = run_scrape(RSS_SOURCES, dry_run=False)
    total = sum(s["new"] for s in stats.values())
    context.log.info(f"news_raw: {total} new articles {stats}")
    return {"new_articles": total, "per_source": stats}


@asset(
    group_name="news",
    description="Fetch FireAnt type=1 news posts per TICKERS → upsert news_articles. Idempotent via postID URL.",
    retry_policy=RetryPolicy(max_retries=2, delay=30),
)
def fireant_news(context: AssetExecutionContext) -> dict:
    from data.fireant import fetch_ticker_news
    from core.db import get_conn
    from data.news_scraper import extract_tickers

    total_new = 0
    per_ticker: dict[str, int] = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            for ticker in TICKERS:
                new_count = 0
                try:
                    posts = fetch_ticker_news(ticker, max_articles=20)
                    for p in posts:
                        title = p.get("title", "").strip()
                        if not title:
                            continue
                        # Use title hash as dedup key (no article URL exposed by FireAnt)
                        url_key = f"https://fireant.vn/news/{hashlib.md5(title.encode()).hexdigest()}"
                        body = p.get("description", "") or title
                        source = f"fireant:{p.get('source', 'fireant')}"
                        pub = p.get("published_at", "")
                        tickers_found = extract_tickers(title, body)
                        if ticker not in tickers_found:
                            tickers_found.insert(0, ticker)

                        cur.execute(
                            """
                            INSERT INTO news_articles (url, title, body, source, published_at, tickers)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (url) DO NOTHING
                            RETURNING id
                            """,
                            (url_key, title, body, source, pub, tickers_found),
                        )
                        if cur.fetchone():
                            new_count += 1
                except Exception as exc:
                    conn.rollback()  # reset aborted-transaction state before next ticker
                    context.log.warning(f"fireant_news {ticker}: {exc}")
                per_ticker[ticker] = new_count
                total_new += new_count

    context.log.info(f"fireant_news: {total_new} new articles {per_ticker}")
    return {"new_articles": total_new, "per_ticker": per_ticker}


@asset(
    group_name="news",
    description="CafeF search per TICKERS → upsert news_articles. Idempotent via article URL.",
    retry_policy=RetryPolicy(max_retries=2, delay=30),
)
def cafef_ticker_news(context: AssetExecutionContext) -> dict:
    from data.cafef_rss import fetch_ticker_news
    from core.db import get_conn
    from data.news_scraper import extract_tickers

    total_new = 0
    per_ticker: dict[str, int] = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            for ticker in TICKERS:
                new_count = 0
                try:
                    articles = fetch_ticker_news(ticker, max_articles=15)
                    for a in articles:
                        title = a.get("title", "").strip()
                        url = a.get("url", "").strip()
                        if not title or not url:
                            continue
                        body = title  # CafeF search returns title only
                        source = "cafef"
                        pub = a.get("published_at", "")
                        tickers_found = extract_tickers(title, body)
                        if ticker not in tickers_found:
                            tickers_found.insert(0, ticker)

                        cur.execute(
                            """
                            INSERT INTO news_articles (url, title, body, source, published_at, tickers)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (url) DO NOTHING
                            RETURNING id
                            """,
                            (url, title, body, source, pub, tickers_found),
                        )
                        if cur.fetchone():
                            new_count += 1
                except Exception as exc:
                    conn.rollback()  # reset aborted-transaction state before next ticker
                    context.log.warning(f"cafef_ticker_news {ticker}: {exc}")
                per_ticker[ticker] = new_count
                total_new += new_count

    context.log.info(f"cafef_ticker_news: {total_new} new articles {per_ticker}")
    return {"new_articles": total_new, "per_ticker": per_ticker}


@asset(
    group_name="news",
    deps=[news_raw, fireant_news, cafef_ticker_news],
    description="Embed unindexed news_articles and upsert into Qdrant news_chunks.",
    retry_policy=RetryPolicy(max_retries=3, delay=60),
)
def news_indexed(context: AssetExecutionContext) -> dict:
    from rag.news_index import index_unindexed_batch

    count = index_unindexed_batch(embed_model=EMBED_MODEL, batch_size=50)
    context.log.info(f"news_indexed: {count} articles indexed")
    return {"indexed": count}


@asset(
    group_name="news",
    description="Purge news_articles and Qdrant points older than 90 days.",
)
def news_purge(context: AssetExecutionContext) -> dict:
    from core.db import get_conn
    from rag.news_index import purge_old_articles_qdrant

    qdrant_deleted = purge_old_articles_qdrant(days_to_keep=90)

    pg_deleted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM news_articles WHERE published_at < NOW() - INTERVAL '90 days'"
            )
            pg_deleted = cur.rowcount
        conn.commit()

    context.log.info(f"news_purge: pg={pg_deleted} qdrant={qdrant_deleted}")
    return {"postgres_deleted": pg_deleted, "qdrant_deleted": qdrant_deleted}
