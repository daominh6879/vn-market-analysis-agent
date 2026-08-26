"""
tests/test_tenant_isolation.py — Bài 17: multi-tenant data isolation.

Tests 3 isolation guarantees:
  1. test_a_cannot_see_b_chunks       — scrolling as tenant_a yields only tenant_a data
  2. test_a_searching_b_content_returns_nothing — vector search filtered to tenant_a
                                        cannot surface tenant_b chunks even with matching vector
  3. test_cache_is_isolated           — cache keys for same query differ by tenant;
                                        wrong (unprefixed) key would share answers across tenants

Also demonstrates post-filter failure (see post_filter_demo below).

Requires: Qdrant running at localhost:6333 (no Ollama needed — uses dummy 4-dim vectors).
Run: uv run pytest tests/test_tenant_isolation.py -v
"""
from __future__ import annotations

import hashlib
import math
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.tenant_search import TenantSearchClient, make_cache_key

TEST_COLLECTION = "test_tenant_isolation_b17"
DIM = 4  # dummy — tests filter logic, not embedding quality

TENANT_A = "tenant_hpg_2024"
TENANT_B = "tenant_hpg_2025"

# Same topic, different numbers — simulates overlapping but distinct tenant datasets
CHUNKS_A = [
    "Doanh thu thuần HPG Q1 2024 đạt 165.000 tỷ đồng",
    "Lợi nhuận gộp HPG 2024 là 22.000 tỷ đồng, biên 13,3%",
    "ROE của HPG năm 2024 đạt 18,5%",
]
CHUNKS_B = [
    "Doanh thu thuần HPG Q1 2025 đạt 280.000 tỷ đồng",
    "Lợi nhuận gộp HPG 2025 là 31.000 tỷ đồng, biên 11,1%",
    "ROE của HPG năm 2025 đạt 21,0%",
]


def _dummy_vec(seed: int) -> list[float]:
    """Deterministic unit-ish vector from seed. Distinct enough to avoid collision."""
    angle = seed * 0.7
    return [math.cos(angle), math.sin(angle), math.cos(angle * 1.3), math.sin(angle * 1.3)]


@pytest.fixture(scope="module")
def search_client() -> TenantSearchClient:
    c = TenantSearchClient(TEST_COLLECTION)
    c.delete_collection()
    c.ensure_collection(dim=DIM)

    for i, text in enumerate(CHUNKS_A):
        c.upsert_chunk(
            point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"a_{i}")),
            vector=_dummy_vec(i),
            text=text,
            tenant_id=TENANT_A,
        )
    for i, text in enumerate(CHUNKS_B):
        c.upsert_chunk(
            point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"b_{i}")),
            vector=_dummy_vec(i + 100),  # large offset — B vectors far from A in cosine space
            text=text,
            tenant_id=TENANT_B,
        )

    yield c
    c.delete_collection()


# ── Tests ────────────────────────────────────────────────────────────────────

def test_a_cannot_see_b_chunks(search_client: TenantSearchClient) -> None:
    """Scrolling as tenant_a returns exactly tenant_a's chunks, no cross-contamination."""
    payloads = search_client.scroll_tenant(TENANT_A)
    assert len(payloads) == len(CHUNKS_A), (
        f"Expected {len(CHUNKS_A)} chunks for {TENANT_A}, got {len(payloads)}"
    )
    for p in payloads:
        assert p.get("tenant_id") == TENANT_A, f"Foreign chunk leaked into tenant_a scroll: {p}"


def test_a_searching_b_content_returns_nothing(search_client: TenantSearchClient) -> None:
    """Vector search scoped to tenant_a cannot surface tenant_b chunks.

    We query using tenant_b's own vector — the closest possible match — to prove
    that the tenant filter blocks it regardless of vector similarity.
    """
    # Use tenant_b chunk 0's vector directly — strongest possible match for b's data
    query_vec = _dummy_vec(100)
    results = search_client.search(TENANT_A, query_vec, top_k=5)
    for text in results:
        assert "2025" not in text, f"Tenant B data (2025) leaked to tenant_a: {text!r}"
        assert "280.000" not in text, f"Tenant B data (280.000) leaked to tenant_a: {text!r}"
        assert "31.000" not in text, f"Tenant B data (31.000) leaked to tenant_a: {text!r}"


def test_cache_is_isolated() -> None:
    """Cache keys for the same query must be distinct per tenant.

    Without tenant prefix, tenant_b receives tenant_a's cached answer verbatim —
    a real production data leak, not just a theoretical concern.
    """
    query = "Doanh thu HPG là bao nhiêu?"

    key_a = make_cache_key(TENANT_A, query)
    key_b = make_cache_key(TENANT_B, query)

    assert key_a != key_b, "Same query → same cache key for different tenants (data leak)"
    assert key_a.startswith(TENANT_A + ":"), f"Cache key must start with tenant_id, got: {key_a}"
    assert key_b.startswith(TENANT_B + ":"), f"Cache key must start with tenant_id, got: {key_b}"

    # Demonstrate the vulnerability: unprefixed key shares cache across tenants
    unsafe_key = hashlib.md5(query.encode()).hexdigest()
    shared_cache: dict[str, str] = {}
    shared_cache[unsafe_key] = "165.000 tỷ (tenant A answer)"
    # tenant_b hits the same key and gets tenant_a's answer:
    assert shared_cache.get(unsafe_key) == "165.000 tỷ (tenant A answer)", (
        "Unsafe key demonstration failed"
    )

    # Correct: isolated cache — tenant_b sees nothing
    safe_cache: dict[str, str] = {}
    safe_cache[key_a] = "165.000 tỷ (tenant A answer)"
    assert safe_cache.get(key_b) is None, "Tenant B should not see Tenant A's cache entry"


# ── Post-filter failure demonstration (not a pass/fail test) ─────────────────

def test_post_filter_silently_reduces_results(search_client: TenantSearchClient) -> None:
    """Proves post-filtering silently returns fewer results than requested.

    With both tenants sharing a collection and only 3 chunks per tenant,
    asking for top_k=5 via post-filter returns < 5 results for a query
    whose top results come from the other tenant.
    """
    # Query vector = B[0]'s exact vector (seed 100, far from A's seeds 0-2)
    query_vec = _dummy_vec(100)

    # top_k=3: with 6 total points, the 3 closest to query_vec are B's chunks.
    # correct filter (tenant_a): Qdrant considers only A's 3 chunks → returns 3
    # post-filter: Qdrant fetches top-3 globally (all B), post-filter drops all → 0
    correct_results = search_client.search(TENANT_A, query_vec, top_k=3)
    post_results = search_client.search_post_filter(TENANT_A, query_vec, top_k=3)

    assert len(post_results) < len(correct_results), (
        "Post-filter should return fewer results than query-time filter "
        f"(correct={len(correct_results)}, post={len(post_results)})"
    )
