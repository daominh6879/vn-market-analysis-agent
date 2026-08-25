"""
rag/fusion.py — Hybrid retrieval fusion: weighted-sum normalization and RRF.

Both functions accept scored result lists — list[tuple[text, score]] sorted
descending — and return a plain list[str] of texts sorted by the fused score.

Why not add scores directly:
  bm25_score = 12.4  (raw BM25 magnitude, corpus-dependent)
  cosine_score = 0.72  (bounded 0-1 cosine)
  12.4 + 0.72 = 13.12 → BM25 dominates regardless of quality.
  Solution: normalize each list to [0,1] first, then combine.
"""
from __future__ import annotations


def weighted_sum_fusion(
    bm25_scored: list[tuple[str, float]],
    vector_scored: list[tuple[str, float]],
    alpha: float = 0.5,
) -> list[str]:
    """Normalize both score lists to [0,1], combine: alpha*bm25 + (1-alpha)*vector."""

    def normalize(pairs: list[tuple[str, float]]) -> dict[str, float]:
        if not pairs:
            return {}
        scores = [s for _, s in pairs]
        min_s, max_s = min(scores), max(scores)
        if max_s == min_s:
            return {text: 0.0 for text, _ in pairs}
        return {text: (s - min_s) / (max_s - min_s) for text, s in pairs}

    bm25_norm = normalize(bm25_scored)
    vector_norm = normalize(vector_scored)

    all_docs = set(bm25_norm) | set(vector_norm)
    fused: dict[str, float] = {}
    for doc in all_docs:
        b = bm25_norm.get(doc, 0.0)
        v = vector_norm.get(doc, 0.0)
        fused[doc] = alpha * b + (1.0 - alpha) * v

    return sorted(fused, key=lambda d: fused[d], reverse=True)


def rrf_fusion(
    bm25_scored: list[tuple[str, float]],
    vector_scored: list[tuple[str, float]],
    k: int = 60,
) -> list[str]:
    """Reciprocal Rank Fusion — uses rank position only, ignores score magnitudes.

    score(doc) = Σ 1/(k + rank_i)  over each retriever that returned doc.
    k=60 is the standard default (Cormack et al. 2009).
    """
    fused: dict[str, float] = {}
    for rank, (doc, _) in enumerate(bm25_scored, start=1):
        fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank)
    for rank, (doc, _) in enumerate(vector_scored, start=1):
        fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(fused, key=lambda d: fused[d], reverse=True)
