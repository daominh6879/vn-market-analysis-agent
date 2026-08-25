"""
evals/eval_reranker.py — So sánh fusion vs 3 biến thể reranker.

4 strategies:
  fusion_ws        — weighted_sum baseline (bài 15, candidate_k=30)
  reranker_512     — CrossEncoder max_length=512, full chunk  (đã đo: 26s/query)
  reranker_256     — CrossEncoder max_length=256, full chunk  (fix timing)
  reranker_snippet — CrossEncoder max_length=256, snippet extraction trước (fix quality)

Tự chấm bằng context_hit@k — không gọi LLM.
Đo timing p50/p95/max cho từng reranker variant.

Usage:
    uv run python evals/eval_reranker.py
    uv run python evals/eval_reranker.py --collection hpg_structural --candidate-k 30
    uv run python evals/eval_reranker.py --skip-512   # bỏ qua variant 26s để chạy nhanh
    uv run python evals/eval_reranker.py --out evals/reranker_results.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import yaml

COLLECTION  = "hpg_structural"
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_URL  = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

GT_KEYWORDS: dict[str, list[str]] = {
    "q08": ["97.018.349.440.000"],
    "q09": ["131"],
    "q10": ["tư vấn quản lý"],
    "q11": ["15 tháng 11 năm 2007"],
    "q12": ["Phạm Thị Kim Oanh"],
    "q13": ["0503000008"],
    "q31": ["98.670.778.691.605"],
    "q32": ["14.074.169.615.158"],
    "q33": ["94.430.926.468.210"],
    "q34": ["Deloitte"],
    "q35": ["14.347.362.462.056"],
    "q37": ["2.859.500.000.000"],
    "q26": ["127 người"],
    "q27": ["mua bán các sản phẩm thép"],
    "q28": ["24 tháng 3 năm 2025"],
    "q29": ["80.585.847.420.000"],
    "q30": ["10.247.400.472.100"],
    "q36": ["81.793.076.515.644"],
    "q38": ["KPMG"],
    "q39": ["80.780.186.578.052"],
    "q40": ["5 công ty con cấp 1"],
}


def load_questions(path: Path) -> list[dict]:
    qs = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    return [q for q in qs
            if q.get("indexed", True)
            and q["group"] not in ("no_answer", "out_of_scope")
            and q["id"] in GT_KEYWORDS]


def hit_at_k(texts: list[str], keywords: list[str], k: int) -> bool:
    blob = " ".join(texts[:k]).lower()
    return all(kw.lower() in blob for kw in keywords)


def retrieve_vector_scored(question: str, collection: str, embed_model: str,
                           top_k: int) -> list[tuple[str, float]]:
    import httpx
    from qdrant_client import QdrantClient

    r = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": embed_model, "prompt": question},
        timeout=60,
    )
    r.raise_for_status()
    qvec = r.json()["embedding"]

    qdrant = QdrantClient("localhost", port=6333)
    points = qdrant.query_points(
        collection_name=collection, query=qvec, limit=top_k
    ).points
    return [(p.payload.get("text", ""), float(p.score)) for p in points]


def percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k",      type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--embed",      default=EMBED_MODEL)
    parser.add_argument("--questions",  default="evals/golden_hpg.yaml")
    parser.add_argument("--out",        default="evals/reranker_results.json")
    parser.add_argument("--alpha",      type=float, default=0.5)
    parser.add_argument("--snippet-window", type=int, default=3,
                        help="Lines before/after best match for snippet extraction (default 3)")
    parser.add_argument("--skip-512",   action="store_true",
                        help="Skip reranker_512 (slow ~26s/query) to save time")
    args = parser.parse_args()

    K  = args.top_k
    CK = args.candidate_k

    questions = load_questions(Path(args.questions))
    print(f"Collection    : {args.collection}")
    print(f"Embed model   : {args.embed}")
    print(f"Questions     : {len(questions)}")
    print(f"Candidate-k   : {CK}  (per retriever, before fusion)")
    print(f"Top-k         : {K}   (final after rerank)")
    print(f"Snippet window: ±{args.snippet_window} lines")
    print(f"Skip 512      : {args.skip_512}")
    print()

    print("Loading BM25 vn retriever...")
    from rag.retrieval_bm25 import BM25Retriever
    from rag.fusion import weighted_sum_fusion
    from rag.reranker import rerank, rerank_with_snippets, extract_snippet

    bm25_vn = BM25Retriever(args.collection, use_vn_tokenize=True)
    print()

    print("Warming up reranker (max_length=256)...")
    rerank("test", ["test doc"], top_k=1, max_length=256)
    if not args.skip_512:
        print("Warming up reranker (max_length=512)...")
        rerank("test", ["test doc"], top_k=1, max_length=512)
    print()

    # Per-strategy timing accumulators
    times: dict[str, list[float]] = {
        "reranker_512":     [],
        "reranker_256":     [],
        "reranker_snippet": [],
    }

    results: list[dict] = []

    for q in questions:
        qid  = q["id"]
        text = q["question"]
        kws  = GT_KEYWORDS[qid]

        print(f"  {qid} [{q['group']:<24}]")

        # Stage 1 — fusion (same for all variants)
        bm25_scored = bm25_vn.search_scored(text, top_k=CK)
        vec_scored  = retrieve_vector_scored(text, args.collection, args.embed, top_k=CK)
        ws_texts    = weighted_sum_fusion(bm25_scored, vec_scored, alpha=args.alpha)
        candidates  = ws_texts[:20]  # top-20 as reranker input

        # fusion baseline
        ws_h5  = hit_at_k(ws_texts, kws, K)
        ws_h10 = hit_at_k(ws_texts, kws, 10)
        ws_h20 = hit_at_k(ws_texts, kws, 20)

        row: dict = {
            "id": qid,
            "group": q["group"],
            "keywords": kws,
            "fusion_ws": {"hit@5": ws_h5, "hit@10": ws_h10, "hit@20": ws_h20},
        }

        ws_mark = "✓" if ws_h5 else "✗"
        print(f"    fusion_ws        @5={ws_mark}")

        # reranker_512 — full chunk, max_length=512 (slow baseline)
        if not args.skip_512:
            t0 = time.perf_counter()
            ranked_512 = rerank(text, candidates, top_k=K, max_length=512)
            t = time.perf_counter() - t0
            times["reranker_512"].append(t)
            texts_512 = [doc for doc, _ in ranked_512]
            h5_512 = hit_at_k(texts_512, kws, K)
            row["reranker_512"] = {"hit@5": h5_512, "t_ms": round(t * 1000, 1)}
            print(f"    reranker_512     @5={'✓' if h5_512 else '✗'}  {t*1000:.0f}ms")
        else:
            row["reranker_512"] = {"hit@5": None, "t_ms": None}

        # reranker_256 — full chunk, max_length=256
        t0 = time.perf_counter()
        ranked_256 = rerank(text, candidates, top_k=K, max_length=256)
        t = time.perf_counter() - t0
        times["reranker_256"].append(t)
        texts_256 = [doc for doc, _ in ranked_256]
        h5_256 = hit_at_k(texts_256, kws, K)
        row["reranker_256"] = {"hit@5": h5_256, "t_ms": round(t * 1000, 1)}
        print(f"    reranker_256     @5={'✓' if h5_256 else '✗'}  {t*1000:.0f}ms")

        # reranker_snippet — snippet extraction then max_length=256
        t0 = time.perf_counter()
        ranked_snip = rerank_with_snippets(text, candidates, top_k=K,
                                           window=args.snippet_window, max_length=256)
        t = time.perf_counter() - t0
        times["reranker_snippet"].append(t)
        texts_snip = [doc for doc, _ in ranked_snip]
        h5_snip = hit_at_k(texts_snip, kws, K)
        row["reranker_snippet"] = {"hit@5": h5_snip, "t_ms": round(t * 1000, 1)}
        print(f"    reranker_snippet @5={'✓' if h5_snip else '✗'}  {t*1000:.0f}ms")

        # Print first snippet for inspection (table_lookup only)
        if q["group"] == "table_lookup" and candidates:
            snip = extract_snippet(text, candidates[0], window=args.snippet_window)
            snip_preview = snip[:120].replace("\n", " ↵ ")
            print(f"    snippet[0]: {snip_preview!r}")

        results.append(row)
        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    n = len(results)
    strategies = [
        ("fusion_ws",        "fusion_ws",        None),
        ("reranker_512",     "reranker_512",      "reranker_512"),
        ("reranker_256",     "reranker_256",      "reranker_256"),
        ("reranker_snippet", "reranker_snippet",  "reranker_snippet"),
    ]

    print("=" * 65)
    print(f"SUMMARY — {n} questions, candidate_k={CK}, top_k={K}")
    print("=" * 65)
    print(f"  {'Strategy':<20}  {'hit@5':>6}  {'p50 ms':>8}  {'p95 ms':>8}")
    print(f"  {'-'*52}")

    summary: dict = {}
    for label, result_key, time_key in strategies:
        if result_key == "fusion_ws":
            hits = sum(1 for r in results if r["fusion_ws"]["hit@5"])
        else:
            hits = sum(1 for r in results
                       if r.get(result_key, {}).get("hit@5"))

        if time_key and times[time_key]:
            p50 = percentile(times[time_key], 50) * 1000
            p95 = percentile(times[time_key], 95) * 1000
            timing_str = f"{p50:>7.0f}   {p95:>7.0f}"
        else:
            p50 = p95 = None
            timing_str = f"{'—':>7}   {'—':>7}" if args.skip_512 and label == "reranker_512" else f"{'':>7}   {'':>7}"

        delta = f"  (+{hits - sum(1 for r in results if r['fusion_ws']['hit@5'])})" if hits > sum(1 for r in results if r['fusion_ws']['hit@5']) else (f"  (-{sum(1 for r in results if r['fusion_ws']['hit@5']) - hits})" if hits < sum(1 for r in results if r['fusion_ws']['hit@5']) else "  (=)")
        print(f"  {label:<20}  {hits}/{n:<4}  {timing_str}{delta}")
        summary[label] = {
            "hit@5": hits,
            "n": n,
            "p50_ms": round(p50, 1) if p50 else None,
            "p95_ms": round(p95, 1) if p95 else None,
        }

    # Per-group
    groups = sorted({r["group"] for r in results})
    print()
    print(f"BY GROUP — hit@5")
    print(f"  {'Group':<24}  {'fusion':>6}  {'rr_512':>6}  {'rr_256':>6}  {'rr_snip':>8}")
    print(f"  {'-'*60}")
    for grp in groups:
        grp_rows = [r for r in results if r["group"] == grp]
        ng = len(grp_rows)
        ws_g   = sum(1 for r in grp_rows if r["fusion_ws"]["hit@5"])
        r512_g = sum(1 for r in grp_rows if r.get("reranker_512", {}).get("hit@5")) if not args.skip_512 else "—"
        r256_g = sum(1 for r in grp_rows if r.get("reranker_256", {}).get("hit@5"))
        rsnp_g = sum(1 for r in grp_rows if r.get("reranker_snippet", {}).get("hit@5"))
        r512_s = f"{r512_g}/{ng}" if not args.skip_512 else "—"
        print(f"  {grp:<24}  {ws_g}/{ng:<4}   {r512_s:<6}   {r256_g}/{ng:<4}   {rsnp_g}/{ng}")

    # Save
    out = {
        "collection": args.collection,
        "candidate_k": CK,
        "top_k": K,
        "snippet_window": args.snippet_window,
        "summary": summary,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved → {args.out}")


if __name__ == "__main__":
    main()
