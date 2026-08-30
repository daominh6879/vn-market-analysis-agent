"""
rag/news_index.py — Qdrant indexer and searcher for news_chunks collection.

Usage:
    python rag/news_index.py --index-all          # embed all unindexed articles
    python rag/news_index.py --index-all --batch 20
    python rag/news_index.py --search "HPG thép" --days 30
    python rag/news_index.py --count              # print collection stats
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    DatetimeRange,
    Distance,
    FieldCondition,
    Filter,
    PointStruct,
    VectorParams,
)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from core.config import settings  # noqa: E402

# ── constants ─────────────────────────────────────────────────────────────────

COLLECTION = "news_chunks"
QDRANT_URL = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
VALID_SENTIMENTS = frozenset({"positive", "neutral", "negative"})

from llm.factory import create_client  # noqa: E402 — module-level for patching
from llm.types import Message  # noqa: E402


# ── sentiment (classify at retrieve time, not index time) ─────────────────────

def classify_sentiment(text: str) -> str:
    """Zero-shot sentiment for Vietnamese financial text."""
    try:
        client = create_client()
        resp = client.generate(
            [Message(role="user", content=text[:500])],
            max_tokens=10,
            system=(
                "Bạn là classifier tài chính. Phân loại cảm xúc của đoạn tin tức tài chính sau.\n"
                "Trả lời đúng một từ: positive, neutral, hoặc negative.\n"
                "Không giải thích. Không dấu câu."
            ),
        )
        label = resp.text.strip().lower().split()[0] if resp.text.strip() else "neutral"
        return label if label in VALID_SENTIMENTS else "neutral"
    except Exception:
        return "neutral"


# ── helpers ───────────────────────────────────────────────────────────────────

def _url_to_uuid(url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()
    return str(uuid.UUID(h))


def _embed(text: str, model: str) -> list[float]:
    r = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def _get_embed_dim(model: str) -> int:
    return len(_embed("test", model))


# ── collection ────────────────────────────────────────────────────────────────

def ensure_news_collection(embed_model: str = DEFAULT_EMBED_MODEL) -> QdrantClient:
    """Create news_chunks if it doesn't exist. Guards against dim mismatch on re-run."""
    client = QdrantClient(url=QDRANT_URL)
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        dim = _get_embed_dim(embed_model)
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"  collection '{COLLECTION}' created (dim={dim})")
    else:
        # B3: verify dim matches to catch OLLAMA_EMBED_MODEL changes after collection creation
        info = client.get_collection(COLLECTION)
        existing_dim = info.config.params.vectors.size
        current_dim = _get_embed_dim(embed_model)
        if existing_dim != current_dim:
            raise RuntimeError(
                f"Embed dim mismatch: collection '{COLLECTION}' has dim={existing_dim} "
                f"but model '{embed_model}' produces dim={current_dim}. "
                f"Drop collection manually or use original model."
            )
        print(f"  collection '{COLLECTION}' exists (dim={existing_dim})")
    return client


# ── indexing ──────────────────────────────────────────────────────────────────

def index_article(client: QdrantClient, article: dict, embed_model: str) -> None:
    """Embed + upsert one article into news_chunks. Idempotent via url hash."""
    text = f"{article['title']}\n{article['body']}"
    vec = _embed(text, embed_model)
    client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=_url_to_uuid(article["url"]),
                vector=vec,
                payload={
                    "text":         text,
                    "title":        article["title"],
                    "source":       article["source"],
                    "published_at": article["published_at"],
                    "tickers":      article.get("tickers", []),
                    "url":          article["url"],
                },
            )
        ],
    )


def index_unindexed_batch(
    embed_model: str = DEFAULT_EMBED_MODEL,
    batch_size: int = 50,
) -> int:
    """Index all news_articles WHERE indexed_at IS NULL. Returns count indexed."""
    from data.db import get_conn
    from data.news_scraper import mark_indexed

    client = ensure_news_collection(embed_model)
    total = 0

    while True:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT url, title, body, source, published_at, tickers
                    FROM news_articles
                    WHERE indexed_at IS NULL
                    ORDER BY published_at DESC
                    LIMIT %s
                    """,
                    (batch_size,),
                )
                rows = cur.fetchall()

        if not rows:
            break

        for row in rows:
            article = {
                "url":          row[0],
                "title":        row[1],
                "body":         row[2],
                "source":       row[3],
                "published_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
                "tickers":      row[5] or [],
            }
            try:
                index_article(client, article, embed_model)
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        mark_indexed(cur, article["url"])
                    conn.commit()
                total += 1
                print(f"  indexed {total}: {article['title'][:60]}", end="\r")
            except Exception as e:
                print(f"\n  [WARN] failed to index {article['url']}: {e}")

    print(f"\n  done — {total} articles indexed")
    return total


# ── search ────────────────────────────────────────────────────────────────────

def search_news_by_text(
    query: str,
    embed_model: str = DEFAULT_EMBED_MODEL,
    days: int = 30,
    limit: int = 5,
    ticker: str | None = None,
) -> list[dict]:
    """Search news_chunks with time filter. Returns list of payload dicts.

    ticker: if provided, add a MatchAny filter on the `tickers` payload field.
    Only effective after backfill_tickers() has been run.
    """
    from qdrant_client.models import MatchAny

    client = QdrantClient(url=QDRANT_URL)
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        return []

    qvec = _embed(query, embed_model)
    # B4: always use RFC-3339 UTC format — Qdrant DatetimeRange requires it
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    must_conditions = [
        FieldCondition(key="published_at", range=DatetimeRange(gte=cutoff)),
    ]
    if ticker:
        must_conditions.append(
            FieldCondition(key="tickers", match=MatchAny(any=[ticker.upper()]))
        )

    try:
        result = client.query_points(
            collection_name=COLLECTION,
            query=qvec,
            query_filter=Filter(must=must_conditions),
            limit=limit,
            with_payload=True,
        )
        points = result.points
    except Exception as e:
        print(f"[WARN] news search failed: {e}")
        return []

    return [p.payload for p in points]


def purge_old_articles_qdrant(days_to_keep: int = 90) -> int:
    """Delete Qdrant points older than days_to_keep. Returns count deleted."""
    client = QdrantClient(url=QDRANT_URL)
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        return 0

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
    result = client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="published_at",
                    range=DatetimeRange(lt=cutoff),
                )
            ]
        ),
    )
    return getattr(result, "deleted_count", 0) or 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="News Qdrant indexer")
    parser.add_argument("--index-all", action="store_true", help="Index all unindexed articles")
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--search", metavar="QUERY", help="Test search query")
    parser.add_argument("--days", type=int, default=30, help="Time window for search (days)")
    parser.add_argument("--count", action="store_true", help="Print collection stats")
    parser.add_argument("--purge", action="store_true", help="Purge articles older than 90 days")
    args = parser.parse_args()

    if args.count:
        client = QdrantClient(url=QDRANT_URL)
        existing = {c.name for c in client.get_collections().collections}
        if COLLECTION in existing:
            info = client.get_collection(COLLECTION)
            print(f"news_chunks: {info.points_count} points")
        else:
            print("news_chunks: collection does not exist yet")
        return

    if args.index_all:
        index_unindexed_batch(embed_model=args.embed_model, batch_size=args.batch)
        return

    if args.search:
        results = search_news_by_text(args.search, embed_model=args.embed_model, days=args.days)
        if not results:
            print("No results (collection empty or no articles in time window)")
            return
        for r in results:
            print(f"[{r.get('published_at', '')[:10]}] {r.get('title', '')}")
            print(f"  source: {r.get('source', '')}  tickers: {r.get('tickers', [])}")
        return

    if args.purge:
        n = purge_old_articles_qdrant()
        print(f"Purged {n} old Qdrant points")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
