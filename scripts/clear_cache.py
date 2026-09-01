"""
scripts/clear_cache.py — wipe Redis exact-cache + Qdrant vector-cache + hybrid memory stores.

Usage:
    python scripts/clear_cache.py                  # clear all tiers
    python scripts/clear_cache.py --redis          # Redis exact tier only
    python scripts/clear_cache.py --qdrant         # Qdrant cache_vectors only
    python scripts/clear_cache.py --user-memory    # Postgres user_memory table only
    python scripts/clear_cache.py --chroma         # ChromaDB chroma_turns only
    python scripts/clear_cache.py --episodes       # Qdrant episodic_memory only
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import settings

_REDIS_PREFIX = "cache:b32:exact"
_QDRANT_COLLECTION = "cache_vectors"
_EPISODES_COLLECTION = "episodic_memory"
_CHROMA_PERSIST_DIR = "./chroma_db"
_CHROMA_COLLECTION = "chat_history"


def clear_redis() -> int:
    import redis as redis_lib
    r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    keys = r.keys(f"{_REDIS_PREFIX}:*")
    if keys:
        r.delete(*keys)
    print(f"Redis: deleted {len(keys)} key(s) matching '{_REDIS_PREFIX}:*'")
    return len(keys)


def clear_qdrant() -> None:
    from qdrant_client import QdrantClient
    client = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    existing = {c.name for c in client.get_collections().collections}
    if _QDRANT_COLLECTION not in existing:
        print(f"Qdrant: collection '{_QDRANT_COLLECTION}' not found — nothing to clear")
        return
    client.delete_collection(_QDRANT_COLLECTION)
    print(f"Qdrant: collection '{_QDRANT_COLLECTION}' deleted")


def clear_user_memory() -> int:
    import psycopg2
    dsn = (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_memory")
            count = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    print(f"user_memory: deleted {count} row(s)")
    return count


def clear_chroma_turns() -> None:
    from langchain_chroma import Chroma
    from langchain_ollama import OllamaEmbeddings
    chroma = Chroma(
        collection_name=_CHROMA_COLLECTION,
        embedding_function=OllamaEmbeddings(model="nomic-embed-text"),
        persist_directory=_CHROMA_PERSIST_DIR,
    )
    result = chroma._collection.get()
    ids = result.get("ids", [])
    if ids:
        chroma._collection.delete(ids=ids)
    print(f"chroma_turns: deleted {len(ids)} document(s) from '{_CHROMA_COLLECTION}'")


def clear_episodes() -> None:
    from qdrant_client import QdrantClient
    client = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    existing = {c.name for c in client.get_collections().collections}
    if _EPISODES_COLLECTION not in existing:
        print(f"episodes: collection '{_EPISODES_COLLECTION}' not found — nothing to clear")
        return
    client.delete_collection(_EPISODES_COLLECTION)
    print(f"episodes: collection '{_EPISODES_COLLECTION}' deleted")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear response cache and hybrid memory stores")
    parser.add_argument("--redis",       action="store_true", help="Redis exact tier only")
    parser.add_argument("--qdrant",      action="store_true", help="Qdrant cache_vectors only")
    parser.add_argument("--user-memory", action="store_true", help="Postgres user_memory table only")
    parser.add_argument("--chroma",      action="store_true", help="ChromaDB chroma_turns only")
    parser.add_argument("--episodes",    action="store_true", help="Qdrant episodic_memory only")
    args = parser.parse_args()

    all_tiers = not any([args.redis, args.qdrant, args.user_memory, args.chroma, args.episodes])

    if args.redis or all_tiers:
        clear_redis()
    if args.qdrant or all_tiers:
        clear_qdrant()
    if args.user_memory or all_tiers:
        clear_user_memory()
    if args.chroma or all_tiers:
        clear_chroma_turns()
    if args.episodes or all_tiers:
        clear_episodes()


if __name__ == "__main__":
    main()
