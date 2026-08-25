"""
evals/eval_all_collections.py — Retrieval-only eval across all 6 HPG collections × 3 retrievers.

No LLM calls. Scores context_hit@1/@3/@5 using GT_KEYWORDS.

Collections tested:
    hpg_b7_fixed_nometa, hpg_b7_structural_nometa, hpg_b7_hier_nometa
    hpg_b7_fixed_meta,   hpg_b7_structural_meta,   hpg_b7_hier_meta

Retrievers per collection:
    vector   — semantic embed search (nomic-embed-text via Ollama)
    bm25     — BM25 raw word split
    bm25_vn  — BM25 with underthesea Vietnamese tokenization

Usage:
    python evals/eval_all_collections.py
    python evals/eval_all_collections.py --top-k 10
    python evals/eval_all_collections.py --out evals/all_collections.json
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

EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

COLLECTIONS = [
    "hpg_b7_fixed_nometa",
    "hpg_b7_structural_nometa",
    "hpg_b7_hier_nometa",
    "hpg_b7_fixed_meta",
    "hpg_b7_structural_meta",
    "hpg_b7_hier_meta",
]

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
    return [q for q in qs if q.get("indexed", True)
            and q["group"] not in ("no_answer", "out_of_scope")]


def hit_at_k(contexts: list[str], keywords: list[str], k: int) -> bool:
    text = " ".join(contexts[:k]).lower()
    return all(kw.lower() in text for kw in keywords)


def retrieve_vector(question: str, collection: str, top_k: int) -> list[str]:
    import httpx
    from qdrant_client import QdrantClient

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    r = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": question},
        timeout=30,
    )
    r.raise_for_status()
    qvec = r.json()["embedding"]

    qdrant = QdrantClient("localhost", port=6333)
    results = qdrant.query_points(
        collection_name=collection, query=qvec, limit=top_k
    ).points
    return [p.payload["text"] for p in results]


def eval_collection(
    collection: str,
    questions: list[dict],
    top_k: int,
) -> dict:
    """Run all 3 retrievers against one collection. Return per-question results + summary."""
    from rag.retrieval_bm25 import BM25Retriever

    print(f"\n{'='*70}")
    print(f"Collection: {collection}")
    print(f"{'='*70}")

    print("  Loading BM25 raw... ", end="", flush=True)
    t0 = time.perf_counter()
    bm25_raw = BM25Retriever(collection, use_vn_tokenize=False)
    print(f"{time.perf_counter()-t0:.1f}s")

    print("  Loading BM25 vn...  ", end="", flush=True)
    t0 = time.perf_counter()
    bm25_vn = BM25Retriever(collection, use_vn_tokenize=True)
    print(f"{time.perf_counter()-t0:.1f}s")

    retrievers = {
        "vector" : lambda q: retrieve_vector(q, collection, top_k),
        "bm25"   : lambda q: bm25_raw.search(q, top_k=top_k),
        "bm25_vn": lambda q: bm25_vn.search(q, top_k=top_k),
    }

    per_question: list[dict] = []

    for q in questions:
        qid  = q["id"]
        text = q["question"]
        grp  = q["group"]
        kws  = GT_KEYWORDS.get(qid)

        row: dict = {
            "id": qid,
            "group": grp,
            "question": text,
            "has_gt_keyword": kws is not None,
            "retrievers": {},
        }

        print(f"  {qid} [{grp:<20}]")

        for rname, fn in retrievers.items():
            t0 = time.perf_counter()
            try:
                ctxs = fn(text)
            except Exception as e:
                print(f"    {rname}: ERROR {e}")
                row["retrievers"][rname] = {"error": str(e)}
                continue
            elapsed = time.perf_counter() - t0

            h1 = hit_at_k(ctxs, kws, 1) if kws else None
            h3 = hit_at_k(ctxs, kws, 3) if kws else None
            h5 = hit_at_k(ctxs, kws, top_k) if kws else None

            s1 = "✓" if h1 else ("?" if h1 is None else "✗")
            s3 = "✓" if h3 else ("?" if h3 is None else "✗")
            s5 = "✓" if h5 else ("?" if h5 is None else "✗")
            print(f"    {rname:<10} @1={s1} @3={s3} @{top_k}={s5}  {elapsed:.1f}s")

            row["retrievers"][rname] = {
                "ctx_hit_at_1": h1,
                "ctx_hit_at_3": h3,
                f"ctx_hit_at_{top_k}": h5,
                "contexts": ctxs,
                "elapsed_s": round(elapsed, 2),
            }

        per_question.append(row)

    # Summary for this collection
    scored = [r for r in per_question if r["has_gt_keyword"]]
    n = len(scored)
    summary: dict[str, dict] = {}
    for rname in retrievers:
        h1 = sum(1 for r in scored if r["retrievers"].get(rname, {}).get("ctx_hit_at_1") is True)
        h3 = sum(1 for r in scored if r["retrievers"].get(rname, {}).get("ctx_hit_at_3") is True)
        hK = sum(1 for r in scored if r["retrievers"].get(rname, {}).get(f"ctx_hit_at_{top_k}") is True)
        summary[rname] = {
            "hit@1": h1, "hit@1_rate": round(h1/n, 3) if n else 0,
            "hit@3": h3, "hit@3_rate": round(h3/n, 3) if n else 0,
            f"hit@{top_k}": hK, f"hit@{top_k}_rate": round(hK/n, 3) if n else 0,
            "n_scored": n,
        }

    print(f"\n  Summary ({n} questions with GT keywords):")
    print(f"  {'Retriever':<12} {'hit@1':>8} {'hit@3':>8} {'hit@'+str(top_k):>8}")
    print(f"  {'-'*40}")
    for rname, s in summary.items():
        print(f"  {rname:<12} {s['hit@1']}/{n:<5}  {s['hit@3']}/{n:<5}  {s[f'hit@{top_k}']}/{n}")

    return {"collection": collection, "top_k": top_k, "summary": summary, "results": per_question}


def print_global_summary(all_results: list[dict], top_k: int) -> None:
    print(f"\n{'='*80}")
    print("GLOBAL SUMMARY — context_hit@" + str(top_k) + " rate")
    print(f"{'='*80}")

    retrievers = ["vector", "bm25", "bm25_vn"]
    header = f"{'Collection':<30}" + "".join(f"  {r:<12}" for r in retrievers)
    print(header)
    print("-" * len(header))

    for res in all_results:
        col = res["collection"]
        row = f"{col:<30}"
        for rname in retrievers:
            s = res["summary"].get(rname, {})
            rate = s.get(f"hit@{top_k}_rate", "N/A")
            hits = s.get(f"hit@{top_k}", "?")
            n    = s.get("n_scored", "?")
            row += f"  {hits}/{n} ({rate:.2f})  " if isinstance(rate, float) else f"  N/A         "
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval all HPG collections — retrieval only, no LLM")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/all_collections_eval.json")
    parser.add_argument("--collections", nargs="*", default=COLLECTIONS,
                        help="Override collection list")
    args = parser.parse_args()

    questions = load_questions(Path(args.questions))
    print(f"Questions : {len(questions)} indexed (skipping no_answer/out_of_scope)")
    print(f"Top-K     : {args.top_k}")
    print(f"Collections: {len(args.collections)}")
    print(f"Retrievers: vector, bm25, bm25_vn")
    print(f"LLM calls : none")
    print(f"Output    : {args.out}")

    all_results: list[dict] = []
    t_start = time.perf_counter()

    for col in args.collections:
        res = eval_collection(col, questions, args.top_k)
        all_results.append(res)

    total_elapsed = time.perf_counter() - t_start
    print_global_summary(all_results, args.top_k)
    print(f"\nTotal time: {total_elapsed:.1f}s")

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(
            {"top_k": args.top_k, "collections": all_results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved → {args.out}")


if __name__ == "__main__":
    main()
