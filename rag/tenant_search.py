"""
rag/tenant_search.py — Multi-tenant isolation for shared RAG systems (Bài 17).

Key principle: filter tenant_id AT QUERY TIME inside Qdrant — never post-filter.

Post-filtering is wrong for two reasons:
  1. Security: all tenants' chunks cross the application boundary before filtering.
     Tenant B's data enters process memory even if not returned to the caller.
  2. Quality: top_k shrinks unpredictably. If 3 of 5 top results belong to
     other tenants, caller silently receives 2 results instead of 5.

Cache isolation: Redis key MUST be prefixed with tenant_id.
  WRONG:  hashlib.md5(query.encode()).hexdigest()
  RIGHT:  f"{tenant_id}:{hashlib.md5(query.encode()).hexdigest()}"
  Without prefix, tenant B receives tenant A's cached answer verbatim.
"""
from __future__ import annotations

import hashlib

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


def make_cache_key(tenant_id: str, query: str) -> str:
    """Tenant-isolated Redis cache key."""
    return f"{tenant_id}:{hashlib.md5(query.encode()).hexdigest()}"


class TenantSearchClient:
    def __init__(
        self,
        collection: str,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
    ) -> None:
        self.collection = collection
        self._client = QdrantClient(qdrant_host, port=qdrant_port)

    def ensure_collection(self, dim: int) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self.collection not in existing:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def upsert_chunk(
        self,
        point_id: str,
        vector: list[float],
        text: str,
        tenant_id: str,
        extra_payload: dict | None = None,
    ) -> None:
        payload = {"text": text, "tenant_id": tenant_id, **(extra_payload or {})}
        self._client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    def search(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int = 5,
    ) -> list[str]:
        """Correct: tenant_id filter applied inside Qdrant at query time."""
        results = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
            ),
            limit=top_k,
        ).points
        return [r.payload.get("text", "") for r in results]

    def search_post_filter(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int = 5,
    ) -> list[str]:
        """WRONG — do not use in production. Demonstrates post-filter failure.

        Fetches top_k from ALL tenants, then filters in Python.
        Problem 1 (security): other tenants' chunks enter app memory.
        Problem 2 (quality): effective top_k is unpredictable — may return fewer
        results than requested if the top results belong to other tenants.
        """
        all_results = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k,  # no tenant filter
        ).points
        return [
            r.payload.get("text", "")
            for r in all_results
            if r.payload.get("tenant_id") == tenant_id
        ]

    def scroll_tenant(self, tenant_id: str, limit: int = 1000) -> list[dict]:
        """Return all chunk payloads belonging to tenant_id."""
        results, _ = self._client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [r.payload for r in results]

    def delete_collection(self) -> None:
        try:
            self._client.delete_collection(self.collection)
        except Exception:
            pass
