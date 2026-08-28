"""
evals/eval_fusion.py — So sánh BM25 / vector / hybrid (weighted-sum, RRF).

Tự chấm điểm bằng context_hit@k — không gọi LLM API.
Retrieves top-CANDIDATE_K from each source, fuses, checks hit@5 / hit@10 / hit@20.

Usage:
    uv run python evals/eval_fusion.py
    uv run python evals/eval_fusion.py --top-k 5 --candidate-k 20
    uv run python evals/eval_fusion.py --collection hpg_structural
    uv run python evals/eval_fusion.py --out evals/fusion_results.json
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

COLLECTION  = "bctc_structural"
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
                           top_k: int, query_filter=None) -> list[tuple[str, float]]:
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
        collection_name=collection, query=qvec, query_filter=query_filter, limit=top_k
    ).points
    return [(p.payload.get("text", ""), float(p.score)) for p in points]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5,
                        help="Final top-k for scoring (default 5)")
    parser.add_argument("--candidate-k", type=int, default=20,
                        help="Candidates retrieved from each source before fusion (default 20)")
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--ticker", default="HPG",
                        help="Ticker filter — comma-separated e.g. HPG or HPG,VCB (default: HPG)")
    parser.add_argument("--embed", default=EMBED_MODEL)
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/fusion_results.json")
    parser.add_argument("--alpha", type=float, default=0.5,
                        help="BM25 weight for weighted-sum fusion (default 0.5)")
    args = parser.parse_args()

    from rag.filter import build_filter
    tickers = [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    ticker_filter = build_filter(tickers=tickers)

    K = args.top_k
    CK = args.candidate_k

    # ── Demo: why direct addition fails ──────────────────────────────────────
    print("=" * 60)
    print("Demo — vì sao cộng thẳng hai loại điểm không được:")
    bm25_example = 12.4
    cosine_example = 0.72
    print(f"  bm25_score   = {bm25_example}")
    print(f"  cosine_score = {cosine_example}")
    print(f"  sum          = {bm25_example + cosine_example:.2f}  ← BM25 chi phối hoàn toàn")
    print(f"  → cần chuẩn hoá về [0,1] trước khi cộng, hoặc dùng RRF (chỉ dùng thứ hạng)")
    print("=" * 60)
    print()

    questions = load_questions(Path(args.questions))
    print(f"Collection  : {args.collection}")
    print(f"Embed model : {args.embed}")
    print(f"Questions   : {len(questions)}")
    print(f"Candidate-k : {CK}  (retrieve from each source)")
    print(f"Top-k       : {K}  (final scoring)")
    print(f"Alpha (WS)  : {args.alpha}  (BM25 weight in weighted-sum)")
    print()

    print("Loading BM25 vn retriever...")
    from rag.retrieval_bm25 import BM25Retriever
    from rag.fusion import weighted_sum_fusion, rrf_fusion

    bm25_vn = BM25Retriever(args.collection, use_vn_tokenize=True, tickers=tickers)
    print()

    STRATEGIES = ["bm25_vn", "vector", "weighted_sum", "rrf"]
    results: list[dict] = []

    for q in questions:
        qid  = q["id"]
        text = q["question"]
        kws  = GT_KEYWORDS[qid]

        print(f"  {qid} [{q['group']}]")

        t0 = time.perf_counter()
        bm25_scored  = bm25_vn.search_scored(text, top_k=CK)
        t_bm25 = time.perf_counter() - t0

        t0 = time.perf_counter()
        vec_scored = retrieve_vector_scored(text, args.collection, args.embed, top_k=CK, query_filter=ticker_filter)
        t_vec = time.perf_counter() - t0

        bm25_texts  = [t for t, _ in bm25_scored]
        vec_texts   = [t for t, _ in vec_scored]
        ws_texts    = weighted_sum_fusion(bm25_scored, vec_scored, alpha=args.alpha)
        rrf_texts   = rrf_fusion(bm25_scored, vec_scored)

        row: dict = {
            "id": qid,
            "group": q["group"],
            "keywords": kws,
            "strategies": {},
            "t_bm25": round(t_bm25, 3),
            "t_vec": round(t_vec, 3),
        }

        for sname, texts in [
            ("bm25_vn", bm25_texts),
            ("vector", vec_texts),
            ("weighted_sum", ws_texts),
            ("rrf", rrf_texts),
        ]:
            h5  = hit_at_k(texts, kws, K)
            h10 = hit_at_k(texts, kws, 10)
            h20 = hit_at_k(texts, kws, 20)
            h5s  = "✓" if h5  else "✗"
            h10s = "✓" if h10 else "✗"
            h20s = "✓" if h20 else "✗"
            print(f"    {sname:<14} @{K}={h5s} @10={h10s} @20={h20s}")
            row["strategies"][sname] = {
                f"hit@{K}": h5,
                "hit@10": h10,
                "hit@20": h20,
                "n_candidates": len(texts),
            }

        results.append(row)

    # ── Summary ───────────────────────────────────────────────────────────────
    n = len(results)
    print()
    print("=" * 65)
    print(f"SUMMARY — {n} questions, candidate_k={CK}")
    print("=" * 65)
    header = f"{'Strategy':<16} {'hit@'+str(K):>7} {'hit@10':>7} {'hit@20':>7}"
    print(header)
    print("-" * 40)

    summary: dict[str, dict] = {}
    for sname in STRATEGIES:
        hK  = sum(1 for r in results if r["strategies"].get(sname, {}).get(f"hit@{K}"))
        h10 = sum(1 for r in results if r["strategies"].get(sname, {}).get("hit@10"))
        h20 = sum(1 for r in results if r["strategies"].get(sname, {}).get("hit@20"))
        print(f"  {sname:<14} {hK}/{n:<4}   {h10}/{n:<4}   {h20}/{n:<4}")
        summary[sname] = {f"hit@{K}": hK, "hit@10": h10, "hit@20": h20, "n": n}

    print()
    # Check if fusion top-20 > individual top-20
    bm25_h20 = summary["bm25_vn"]["hit@20"]
    vec_h20  = summary["vector"]["hit@20"]
    ws_h20   = summary["weighted_sum"]["hit@20"]
    rrf_h20  = summary["rrf"]["hit@20"]
    best_single = max(bm25_h20, vec_h20)

    print("Fusion@20 vs best single@20:")
    ws_better  = "✓ BETTER" if ws_h20  > best_single else ("= EQUAL" if ws_h20 == best_single else "✗ WORSE")
    rrf_better = "✓ BETTER" if rrf_h20 > best_single else ("= EQUAL" if rrf_h20 == best_single else "✗ WORSE")
    print(f"  weighted_sum: {ws_h20}/{n}  vs best_single: {best_single}/{n}  → {ws_better}")
    print(f"  rrf         : {rrf_h20}/{n}  vs best_single: {best_single}/{n}  → {rrf_better}")

    # ── Per-group ─────────────────────────────────────────────────────────────
    groups = sorted({r["group"] for r in results})
    print()
    print(f"BY GROUP — hit@{K}")
    print(f"  {'Group':<22}", end="")
    for s in STRATEGIES:
        print(f"  {s:<14}", end="")
    print()
    print("  " + "-" * (22 + len(STRATEGIES) * 16))
    for grp in groups:
        grp_rows = [r for r in results if r["group"] == grp]
        ng = len(grp_rows)
        print(f"  {grp:<22}", end="")
        for sname in STRATEGIES:
            hits = sum(1 for r in grp_rows if r["strategies"].get(sname, {}).get(f"hit@{K}"))
            print(f"  {hits}/{ng:<12}", end="")
        print()

    # ── Save ──────────────────────────────────────────────────────────────────
    out = {
        "collection": args.collection,
        "embed": args.embed,
        "top_k": K,
        "candidate_k": CK,
        "alpha": args.alpha,
        "summary": summary,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved → {args.out}")


if __name__ == "__main__":
    main()
