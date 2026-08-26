#!/usr/bin/env python3
"""
evals/eval_rag_fusion.py — Compare single-query vs multi-query RAG-Fusion.

Measures:
  - recall@5: fraction of questions where ground-truth keywords appear in top-5 chunks
  - context_recall: RAGAS ContextRecall (optional, --ragas)
  - p95 latency
  - LLM cost per question (token count)

Usage:
    python evals/eval_rag_fusion.py --collection hpg_fixed_512 --embed nomic-embed-text
    python evals/eval_rag_fusion.py --collection hpg_fixed_512 --embed nomic-embed-text --ragas
    python evals/eval_rag_fusion.py --collection hpg_fixed_512 --n 3 --questions evals/golden_hpg.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from pathlib import Path

# RAGAS compat shim (same as run.py)
try:
    from langchain_google_vertexai import ChatVertexAI as _ChatVertexAI
    from langchain_google_vertexai import VertexAI as _VertexAI
    _cv = types.ModuleType("langchain_community.chat_models.vertexai")
    _cv.ChatVertexAI = _ChatVertexAI
    sys.modules.setdefault("langchain_community.chat_models.vertexai", _cv)
    _lv = types.ModuleType("langchain_community.llms.vertexai")
    _lv.VertexAI = _VertexAI
    sys.modules.setdefault("langchain_community.llms.vertexai", _lv)
except ImportError:
    pass

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from rag.retrieval_bm25 import BM25Retriever
from rag.fusion import rrf_fusion
from rag.multi_query import generate_sub_queries


# ── Retrieval helpers ──────────────────────────────────────────────────────────

def _embed(query: str, embed_model: str) -> list[float]:
    import httpx
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    r = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": embed_model, "prompt": query},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def single_query_retrieve(
    query: str,
    collection: str,
    embed_model: str,
    bm25_retriever: BM25Retriever,
    top_k: int = 5,
    candidate_k: int = 20,
) -> list[str]:
    from qdrant_client import QdrantClient
    qvec = _embed(query, embed_model)
    qdrant = QdrantClient("localhost", port=6333)
    points = qdrant.query_points(collection_name=collection, query=qvec, limit=candidate_k).points
    vec_scored = [(p.payload["text"], float(p.score)) for p in points]
    bm25_scored = bm25_retriever.search_scored(query, top_k=candidate_k)
    fused = rrf_fusion(bm25_scored, vec_scored)
    return fused[:top_k]


def multi_query_retrieve(
    query: str,
    collection: str,
    embed_model: str,
    bm25_retriever: BM25Retriever,
    n: int = 4,
    top_k: int = 5,
    candidate_k: int = 10,
) -> tuple[list[str], list[str]]:
    """Returns (fused_chunks, sub_queries)."""
    import asyncio

    sub_queries = generate_sub_queries(query, n=n)

    # Parallel async retrieval
    async def _retrieve_all():
        import asyncio
        from qdrant_client import QdrantClient

        async def retrieve_one(sq: str) -> list[tuple[str, float]]:
            loop = asyncio.get_event_loop()
            qvec = await loop.run_in_executor(None, _embed, sq, embed_model)
            qdrant = QdrantClient("localhost", port=6333)
            points = qdrant.query_points(collection_name=collection, query=qvec, limit=candidate_k).points
            vec_scored = [(p.payload["text"], float(p.score)) for p in points]
            bm25_scored = bm25_retriever.search_scored(sq, top_k=candidate_k)
            fused = rrf_fusion(bm25_scored, vec_scored)
            return [(t, 1.0) for t in fused]

        return await asyncio.gather(*[retrieve_one(sq) for sq in sub_queries])

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        all_scored = loop.run_until_complete(_retrieve_all())
    except RuntimeError:
        all_scored = asyncio.run(_retrieve_all())

    # Multi-list RRF fold
    if len(all_scored) == 1:
        fused = [t for t, _ in all_scored[0]]
    else:
        current = all_scored[0]
        for nxt in all_scored[1:]:
            merged = rrf_fusion(current, nxt)
            current = [(t, 1.0) for t in merged]
        fused = [t for t, _ in current]

    return fused[:top_k], sub_queries


# ── Recall metric ──────────────────────────────────────────────────────────────

def recall_at_k(chunks: list[str], ground_truth: str) -> float:
    """Keyword-based recall: does any chunk contain significant GT keywords?"""
    gt_words = set(ground_truth.lower().split())
    # Filter stopwords
    stopwords = {"là", "của", "và", "trong", "có", "các", "với", "về", "được",
                 "không", "này", "cho", "một", "những", "theo", "tại", "từ"}
    gt_words -= stopwords
    if not gt_words:
        return 0.0
    for chunk in chunks:
        chunk_lower = chunk.lower()
        overlap = sum(1 for w in gt_words if w in chunk_lower)
        if overlap / len(gt_words) >= 0.3:
            return 1.0
    return 0.0


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="RAG-Fusion eval: single vs multi-query")
    parser.add_argument("--collection", default="hpg_b7_structural_meta", help="Qdrant collection name")
    parser.add_argument("--embed", default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--n", type=int, default=4, help="Number of sub-queries")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="Limit questions for fast test")
    parser.add_argument("--ragas", action="store_true", help="Run RAGAS ContextRecall (slow)")
    parser.add_argument("--out", default="evals/rag_fusion_eval.json")
    args = parser.parse_args()

    questions_raw = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    questions = [q for q in questions_raw if q["group"] not in ("no_answer", "out_of_scope")
                 and q.get("indexed", True)]
    if args.limit:
        questions = questions[:args.limit]

    print(f"Collection : {args.collection}")
    print(f"Embed      : {args.embed}")
    print(f"Questions  : {len(questions)}")
    print(f"Sub-queries: N={args.n}")
    print()

    bm25 = BM25Retriever(collection=args.collection, use_vn_tokenize=True)

    single_latencies: list[float] = []
    multi_latencies:  list[float] = []
    single_recalls:   list[float] = []
    multi_recalls:    list[float] = []

    single_samples: list[dict] = []
    multi_samples:  list[dict] = []

    for q in questions:
        qtext = q["question"]
        gt    = q["answer"]
        print(f"  {q['id']:6s}  [{q['group']:<20}]", end="", flush=True)

        # Single query
        t0 = time.perf_counter()
        s_chunks = single_query_retrieve(qtext, args.collection, args.embed, bm25,
                                         top_k=args.top_k, candidate_k=args.candidate_k * 2)
        s_lat = time.perf_counter() - t0
        s_recall = recall_at_k(s_chunks, gt)
        single_latencies.append(s_lat)
        single_recalls.append(s_recall)
        single_samples.append({"question": qtext, "contexts": s_chunks, "ground_truth": gt})

        # Multi query
        t0 = time.perf_counter()
        m_chunks, sub_qs = multi_query_retrieve(qtext, args.collection, args.embed, bm25,
                                                 n=args.n, top_k=args.top_k,
                                                 candidate_k=args.candidate_k)
        m_lat = time.perf_counter() - t0
        m_recall = recall_at_k(m_chunks, gt)
        multi_latencies.append(m_lat)
        multi_recalls.append(m_recall)
        multi_samples.append({"question": qtext, "contexts": m_chunks, "ground_truth": gt,
                               "sub_queries": sub_qs})

        print(f"  single recall={s_recall:.0f} {s_lat:.1f}s | multi recall={m_recall:.0f} {m_lat:.1f}s")

    # Stats
    import statistics

    def p95(vals: list[float]) -> float:
        if not vals:
            return 0.0
        vals_sorted = sorted(vals)
        idx = int(len(vals_sorted) * 0.95)
        return vals_sorted[min(idx, len(vals_sorted) - 1)]

    single_recall_avg = sum(single_recalls) / len(single_recalls) if single_recalls else 0.0
    multi_recall_avg  = sum(multi_recalls)  / len(multi_recalls)  if multi_recalls  else 0.0
    single_p95 = p95(single_latencies)
    multi_p95  = p95(multi_latencies)

    print("\n" + "=" * 60)
    print("| Cấu hình              | recall@5 | Thời gian p95 |")
    print("|" + "-" * 59 + "|")
    print(f"| Single query          | {single_recall_avg:.3f}    | {single_p95:.1f}s          |")
    print(f"| Multi-query (N={args.n}) + RRF | {multi_recall_avg:.3f}    | {multi_p95:.1f}s          |")
    print("=" * 60)

    result = {
        "config": {
            "collection": args.collection,
            "embed": args.embed,
            "n_sub_queries": args.n,
            "top_k": args.top_k,
            "n_questions": len(questions),
        },
        "single_query": {
            "recall_at_k_avg": single_recall_avg,
            "latency_p95_s": single_p95,
            "latencies": single_latencies,
            "recalls": single_recalls,
        },
        "multi_query": {
            "recall_at_k_avg": multi_recall_avg,
            "latency_p95_s": multi_p95,
            "latencies": multi_latencies,
            "recalls": multi_recalls,
        },
        "samples": {
            "single": single_samples,
            "multi": multi_samples,
        },
    }

    if args.ragas and single_samples:
        print("\nComputing RAGAS ContextRecall (slow)...")
        try:
            from evals.run import compute_ragas
            ragas_provider = os.environ.get("RAGAS_PROVIDER", "ollama")
            ollama_model = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
            ollama_embed = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

            # RAGAS needs answer field — use placeholder since we only care about context_recall
            for s in single_samples:
                s.setdefault("answer", s["ground_truth"])
            for s in multi_samples:
                s.setdefault("answer", s["ground_truth"])

            s_ragas = compute_ragas(single_samples, ragas_provider, ollama_model, ollama_embed)
            m_ragas = compute_ragas(multi_samples,  ragas_provider, ollama_model, ollama_embed)
            result["single_query"]["ragas"] = s_ragas
            result["multi_query"]["ragas"]  = m_ragas
            print(f"RAGAS single context_recall: {s_ragas.get('context_recall', 'n/a'):.3f}")
            print(f"RAGAS multi  context_recall: {m_ragas.get('context_recall', 'n/a'):.3f}")
        except Exception as e:
            print(f"RAGAS failed: {e}")

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults → {args.out}")


if __name__ == "__main__":
    main()
