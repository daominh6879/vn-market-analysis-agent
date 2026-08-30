"""
scripts/clear_cache.py — wipe Redis exact-cache + Qdrant vector-cache for bài 32.

Usage:
    python scripts/clear_cache.py           # clear both tiers
    python scripts/clear_cache.py --redis   # Redis only
    python scripts/clear_cache.py --qdrant  # Qdrant only
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import settings

_REDIS_PREFIX = "cache:b32:exact"
_QDRANT_COLLECTION = "cache_vectors"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear bài-32 response cache")
    parser.add_argument("--redis",  action="store_true", help="Redis exact tier only")
    parser.add_argument("--qdrant", action="store_true", help="Qdrant vector tier only")
    args = parser.parse_args()

    both = not args.redis and not args.qdrant

    if args.redis or both:
        clear_redis()
    if args.qdrant or both:
        clear_qdrant()


if __name__ == "__main__":
    main()
