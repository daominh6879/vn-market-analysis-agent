"""
pipeline/assets_news.py — Dagster assets for news pipeline.

Assets:
  news_raw     — scrape RSS CafeF + VnExpress → upsert news_articles (every 6h)
  news_indexed — embed unindexed articles → upsert news_chunks (after news_raw)
  news_purge   — delete articles + Qdrant points older than 90 days (weekly)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from dagster import AssetExecutionContext, RetryPolicy, asset

EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


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
    deps=[news_raw],
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
    from data.db import get_conn
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
