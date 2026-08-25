"""
evals/compare_retrievers.py — So sánh BM25 raw / BM25 vn / vector, tự chấm điểm.

Chấm điểm không dùng LLM:
  context_hit@k  = ground-truth keyword xuất hiện trong top-k contexts (0/1)
  answer = đúng nếu model trả lời có keyword (dùng LLM client, skip nếu --no-llm)

Usage:
    uv run python evals/compare_retrievers.py
    uv run python evals/compare_retrievers.py --no-llm   # chỉ chấm context, không gọi model
    uv run python evals/compare_retrievers.py --top-k 5
    uv run python evals/compare_retrievers.py --out evals/compare.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import yaml

COLLECTION   = "hpg_structural"
EMBED_MODEL  = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# ── Ground-truth keywords per question ───────────────────────────────────────
# Lấy chuỗi đặc trưng nhất từ answer — số cụ thể, mã, cụm từ độc nhất.
# Nếu answer có nhiều chuỗi, lấy cái ngắn nhất vẫn đủ discriminative.

GT_KEYWORDS: dict[str, list[str]] = {
    # 2025 PDF
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
    # 2024 PDF
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_questions(path: Path) -> list[dict]:
    qs = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    return [q for q in qs if q.get("indexed", True)
            and q["group"] not in ("no_answer", "out_of_scope")]


def context_hit(contexts: list[str], keywords: list[str]) -> bool:
    text = " ".join(contexts).lower()
    return all(kw.lower() in text for kw in keywords)


def hit_at_k(contexts: list[str], keywords: list[str], k: int) -> bool:
    return context_hit(contexts[:k], keywords)


def retrieve_vector(question: str, top_k: int) -> list[str]:
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
        collection_name=COLLECTION, query=qvec, limit=top_k
    ).points
    return [p.payload["text"] for p in results]


def ask_model(client, question: str, contexts: list[str]) -> str:
    from llm.types import Message
    ctx_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'.\n\n"
        f"TÀI LIỆU:\n{ctx_block}"
    )
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=256,
        system=system,
    )
    return resp.text


def answer_hit(answer: str, keywords: list[str]) -> bool:
    a = answer.lower()
    return all(kw.lower() in a for kw in keywords)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip model calls — score context only")
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/compare.json")
    args = parser.parse_args()

    K = args.top_k
    questions = load_questions(Path(args.questions))
    print(f"Questions: {len(questions)} indexed (skip no_answer/out_of_scope)")
    print(f"Top-k    : {K}")
    print(f"LLM calls: {'NO (--no-llm)' if args.no_llm else 'YES'}")
    print()

    # Build retrievers
    print("Loading BM25 raw...")
    from rag.retrieval_bm25 import BM25Retriever
    bm25_raw = BM25Retriever(COLLECTION, use_vn_tokenize=False)

    print("Loading BM25 vn...")
    bm25_vn = BM25Retriever(COLLECTION, use_vn_tokenize=True)

    llm_client = None
    if not args.no_llm:
        from llm.factory import create_client
        llm_client = create_client()

    RETRIEVERS = {
        "bm25_raw": lambda q: bm25_raw.search(q, top_k=K),
        "bm25_vn" : lambda q: bm25_vn.search(q, top_k=K),
        "vector"  : lambda q: retrieve_vector(q, top_k=K),
    }

    results: list[dict] = []

    for q in questions:
        qid  = q["id"]
        text = q["question"]
        grp  = q["group"]
        kws  = GT_KEYWORDS.get(qid)

        row: dict = {"id": qid, "group": grp, "question": text, "retrievers": {}}

        print(f"  {qid} [{grp}]")

        for rname, retrieve_fn in RETRIEVERS.items():
            t0 = time.perf_counter()
            try:
                ctxs = retrieve_fn(text)
            except Exception as e:
                print(f"    {rname}: ERROR {e}")
                row["retrievers"][rname] = {"error": str(e)}
                continue
            elapsed = time.perf_counter() - t0

            ctx_h1 = hit_at_k(ctxs, kws, 1) if kws else None
            ctx_h3 = hit_at_k(ctxs, kws, 3) if kws else None
            ctx_h5 = hit_at_k(ctxs, kws, K) if kws else None

            ans_hit = None
            answer  = None
            if not args.no_llm and llm_client and kws:
                answer  = ask_model(llm_client, text, ctxs)
                ans_hit = answer_hit(answer, kws)

            h1 = "✓" if ctx_h1 else ("?" if ctx_h1 is None else "✗")
            h3 = "✓" if ctx_h3 else ("?" if ctx_h3 is None else "✗")
            h5 = "✓" if ctx_h5 else ("?" if ctx_h5 is None else "✗")
            ah = ("✓" if ans_hit else "✗") if ans_hit is not None else "-"
            print(f"    {rname:<10} ctx@1={h1} @3={h3} @{K}={h5}  ans={ah}  {elapsed:.1f}s")

            row["retrievers"][rname] = {
                "ctx_hit_at_1": ctx_h1,
                "ctx_hit_at_3": ctx_h3,
                f"ctx_hit_at_{K}": ctx_h5,
                "answer_hit": ans_hit,
                "answer": answer,
                "contexts": ctxs,
                "elapsed": round(elapsed, 2),
            }

        results.append(row)

    # ── Summary table ─────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"SUMMARY — context_hit@{K}  (only questions with GT_KEYWORDS)")
    print("=" * 70)

    scored = [r for r in results if GT_KEYWORDS.get(r["id"])]
    n = len(scored)

    header = f"{'Retriever':<12} {'hit@1':>6} {'hit@3':>6} {'hit@5':>6}"
    if not args.no_llm:
        header += f" {'ans_hit':>8}"
    print(header)
    print("-" * (len(header) + 2))

    summary: dict[str, dict] = {}
    for rname in RETRIEVERS:
        h1 = sum(1 for r in scored
                 if r["retrievers"].get(rname, {}).get("ctx_hit_at_1") is True)
        h3 = sum(1 for r in scored
                 if r["retrievers"].get(rname, {}).get("ctx_hit_at_3") is True)
        hK = sum(1 for r in scored
                 if r["retrievers"].get(rname, {}).get(f"ctx_hit_at_{K}") is True)
        ah = sum(1 for r in scored
                 if r["retrievers"].get(rname, {}).get("answer_hit") is True)

        line = f"{rname:<12} {h1}/{n:>3}  {h3}/{n:>3}  {hK}/{n:>3}"
        if not args.no_llm:
            line += f"  {ah}/{n:>5}"
        print(line)
        summary[rname] = {"hit@1": h1, "hit@3": h3, f"hit@{K}": hK,
                           "answer_hit": ah, "n": n}

    # ── Per-group breakdown ───────────────────────────────────────────────────
    groups = sorted({r["group"] for r in scored})
    print()
    print(f"BY GROUP — context_hit@{K}")
    print(f"{'Group':<22}", end="")
    for rname in RETRIEVERS:
        print(f"  {rname:<10}", end="")
    print()
    print("-" * (22 + len(RETRIEVERS) * 12))

    for grp in groups:
        grp_rows = [r for r in scored if r["group"] == grp]
        ng = len(grp_rows)
        print(f"  {grp:<20}", end="")
        for rname in RETRIEVERS:
            hits = sum(1 for r in grp_rows
                       if r["retrievers"].get(rname, {}).get(f"ctx_hit_at_{K}") is True)
            print(f"  {hits}/{ng:<9}", end="")
        print()

    # ── Save ──────────────────────────────────────────────────────────────────
    out = {
        "summary": summary,
        "top_k": K,
        "results": results,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved → {args.out}")


if __name__ == "__main__":
    main()
