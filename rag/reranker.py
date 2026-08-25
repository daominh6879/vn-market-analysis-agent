"""
rag/reranker.py — CrossEncoder reranker using BAAI/bge-reranker-v2-m3.

Two-stage retrieval: broad candidate retrieval (top-20) → rerank → top-5.

Why cross-encoder is more accurate than bi-encoder:
  Bi-encoder encodes query and doc separately, then compares vectors.
  Cross-encoder reads (query + doc) together — every query token can attend
  to every doc token — so it captures exact relevance, not just topic similarity.

Why we can't run cross-encoder on the full corpus:
  O(n) predictions on CPU: 800 chunks × 30ms = 24s per query.
  Solution: bi-encoder narrows to top-20 candidates, cross-encoder re-scores only those.

Architectural mismatch with structural chunks (financial tables):
  Structural chunks = full markdown table sections (~450 tokens).
  Cross-encoder scores (query, 50-row-table) → signal diluted by irrelevant rows.
  Fix A: max_length=256 (cuts attention cost ~4x, may truncate relevant numbers).
  Fix B: extract_snippet() — find most relevant lines before scoring.
  Fix B is preferred for table-heavy domains.

Lost-in-middle effect:
  LLMs attend more to context at start and end of the window.
  rerank_for_llm() moves the highest-scored chunk to the last position.
"""
from __future__ import annotations

_model_512 = None
_model_256 = None


def _get_model(max_length: int = 512):
    global _model_512, _model_256
    if max_length == 256:
        if _model_256 is None:
            from sentence_transformers import CrossEncoder
            _model_256 = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=256)
        return _model_256
    else:
        if _model_512 is None:
            from sentence_transformers import CrossEncoder
            _model_512 = CrossEncoder("BAAI/bge-reranker-v2-m3")
        return _model_512


def extract_snippet(query: str, chunk: str, window: int = 3) -> str:
    """Return the most query-relevant lines from a chunk ± window lines.

    Why: structural chunks are full table sections (~50 rows). Cross-encoder
    scoring (query, 50-row-table) dilutes the relevance signal. Extracting
    the best-matching lines gives the model a focused, short input.

    The caller reranks using snippets but passes full chunks to the LLM —
    so the LLM still gets the original context.
    """
    lines = [ln for ln in chunk.split("\n") if ln.strip()]
    if not lines:
        return chunk

    qtokens = set(query.lower().split())
    def line_score(ln: str) -> int:
        return len(set(ln.lower().split()) & qtokens)

    best = max(range(len(lines)), key=lambda i: line_score(lines[i]))
    start = max(0, best - window)
    end   = min(len(lines), best + window + 1)
    return "\n".join(lines[start:end])


def rerank(
    query: str,
    candidates: list[str],
    top_k: int = 5,
    max_length: int = 512,
) -> list[tuple[str, float]]:
    """Score (query, candidate) pairs. Return top_k sorted by score desc."""
    if not candidates:
        return []
    model = _get_model(max_length)
    pairs = [(query, doc) for doc in candidates]
    scores = model.predict(pairs)
    ranked = sorted(zip(candidates, scores.tolist()), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def rerank_with_snippets(
    query: str,
    candidates: list[str],
    top_k: int = 5,
    window: int = 3,
    max_length: int = 256,
) -> list[tuple[str, float]]:
    """Rerank using per-chunk snippets; return original full chunk texts.

    Scoring is done on short focused snippets → better signal for table chunks.
    Returned tuples contain the original (full) chunk text, not the snippet.
    """
    if not candidates:
        return []
    snippets = [extract_snippet(query, c, window) for c in candidates]
    model = _get_model(max_length)
    pairs = [(query, s) for s in snippets]
    scores = model.predict(pairs)
    ranked = sorted(
        zip(candidates, scores.tolist()),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[:top_k]


def rerank_for_llm(
    query: str,
    candidates: list[str],
    top_k: int = 5,
    use_snippets: bool = True,
    max_length: int = 256,
) -> list[str]:
    """Rerank then apply lost-in-middle ordering (best chunk moved to last position)."""
    if use_snippets:
        ranked = rerank_with_snippets(query, candidates, top_k, max_length=max_length)
    else:
        ranked = rerank(query, candidates, top_k, max_length=max_length)
    texts = [t for t, _ in ranked]
    if len(texts) > 1:
        texts = texts[1:] + [texts[0]]
    return texts
