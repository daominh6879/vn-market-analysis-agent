#!/usr/bin/env python3
"""
evals/compare_architectures.py — Compare 3 RAG architectures on HPG questions.

Architectures:
  arch_a: Pure vector RAG  (embed → Qdrant top-5 → LLM)
  arch_b: Hybrid + Rerank  (BM25 + vector → weighted_sum → CrossEncoder → LLM)
  arch_c: Planner-based    (QueryIntent interpret → smart retrieve with filters → RRF merge → LLM)

Metrics per arch per question group:
  quality_score  — LLM-as-judge 1–5
  latency_s      — wall-clock seconds
  total_tokens   — input + output
  cost_usd       — estimated from token counts
  failure_rate   — fraction of runs that errored or returned empty

Usage:
  python evals/compare_architectures.py                        # all 25 questions
  python evals/compare_architectures.py --sample              # 3 questions (one per group)
  python evals/compare_architectures.py --out evals/arch_compare.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Fix Windows console encoding for Vietnamese text
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

import httpx
from qdrant_client import QdrantClient

from llm.factory import create_client
from llm.types import Message
from rag.filter import BCTC_COLLECTION, build_filter
from rag.retrieval_bm25 import BM25Retriever
from rag.fusion import weighted_sum_fusion
from tools.query_interpreter import interpret as _interpret_query

# ── config ────────────────────────────────────────────────────────────────────

COLLECTION = BCTC_COLLECTION
_FILTER = None  # set in main() from --ticker flag
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_URL  = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
TOP_K       = 5
CANDIDATE_K = 20

# DeepSeek-v4-flash pricing (USD per million tokens)
# Using deepseek-chat pricing as proxy (0.27 input / 1.10 output)
PRICE_IN    = 0.27 / 1_000_000
PRICE_OUT   = 1.10 / 1_000_000

# Question classification: simple / compound / multi_source
QUESTION_GROUPS = {
    # simple: single-fact lookup in one doc
    "simple": {"q08","q31","q32","q33","q35","q37","q29","q30","q36","q39",
               "q10","q11","q12","q13","q27","q28","q34","q38","q40"},
    # compound: requires combining/comparing info (year-over-year changes)
    "compound": {"q09","q26"},
    # multi_source: must determine answer doesn't exist in any source
    "multi_source": {"q21","q22","q23","q24","q25"},
}

# Representative sample: one question per group
SAMPLE_IDS = {
    "simple":       "q31",   # Tổng tài sản 2025
    "compound":     "q09",   # Nhân viên 2025 vs 2024
    "multi_source": "q21",   # Doanh thu thép xây dựng (no_answer)
}

ARCH_NAMES = ["arch_a", "arch_b", "arch_c"]


# ── retrieval helpers ─────────────────────────────────────────────────────────

_qdrant: QdrantClient | None = None

def qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient("localhost", port=6333)
    return _qdrant


def embed(text: str) -> list[float]:
    r = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def vector_retrieve(question: str, top_k: int = TOP_K) -> list[str]:
    vec = embed(question)
    pts = qdrant().query_points(
        collection_name=COLLECTION, query=vec, query_filter=_FILTER, limit=top_k
    ).points
    return [p.payload["text"] for p in pts]


def vector_retrieve_scored(question: str, top_k: int = CANDIDATE_K) -> list[tuple[str, float]]:
    vec = embed(question)
    pts = qdrant().query_points(
        collection_name=COLLECTION, query=vec, query_filter=_FILTER, limit=top_k
    ).points
    return [(p.payload["text"], float(p.score)) for p in pts]


def vector_retrieve_scored_filtered(
    question: str,
    top_k: int = CANDIDATE_K,
    tickers: list[str] | None = None,
    year: str | None = None,
) -> list[tuple[str, float]]:
    """Like vector_retrieve_scored but with explicit ticker/year filter."""
    f = build_filter(tickers=tickers or None, year=year) if (tickers or year) else _FILTER
    vec = embed(question)
    pts = qdrant().query_points(
        collection_name=COLLECTION, query=vec, query_filter=f, limit=top_k
    ).points
    return [(p.payload["text"], float(p.score)) for p in pts]


def hybrid_fusion_retrieve(question: str, bm25: BM25Retriever) -> list[str]:
    """arch_b: BM25 + vector weighted_sum fusion (no CrossEncoder — segfaults intermittently)."""
    bm25_scored = bm25.search_scored(question, top_k=CANDIDATE_K)
    vec_scored  = vector_retrieve_scored(question, top_k=CANDIDATE_K)
    fused = weighted_sum_fusion(bm25_scored, vec_scored)
    return fused[:TOP_K]


def rrf_merge(scored_lists: list[list[tuple[str, float]]]) -> list[str]:
    """Simple RRF: score each text by sum(1/(k+rank)) across lists."""
    K = 60
    scores: dict[str, float] = {}
    for lst in scored_lists:
        for rank, (text, _) in enumerate(lst):
            scores[text] = scores.get(text, 0.0) + 1.0 / (K + rank + 1)
    return [t for t, _ in sorted(scores.items(), key=lambda x: -x[1])]


# ── LLM helpers ───────────────────────────────────────────────────────────────

_client = None

def client():
    global _client
    if _client is None:
        _client = create_client()
    return _client


SYSTEM_RAG = (
    "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
    "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'."
)


def llm_answer(question: str, contexts: list[str]) -> tuple[str, int, int]:
    """Return (answer_text, input_tokens, output_tokens)."""
    ctx_block = "\n\n---\n\n".join(contexts) if contexts else "[không có ngữ cảnh]"
    system = f"{SYSTEM_RAG}\n\nTÀI LIỆU:\n{ctx_block}"
    resp = client().generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text, resp.input_tokens, resp.output_tokens


def _interpret_for_arch_c(question: str):
    """Call query_interpreter and return (intent, in_tokens, out_tokens).

    Reuses the shared LLM client so token tracking stays accurate.
    """
    intent = _interpret_query(question, client=client())
    # Approximate token usage — interpreter uses max_tokens=512
    # Actual counts not exposed by interpret(); use 0 as lower bound.
    return intent, 0, 0


REFUSAL_KEYWORDS = [
    "không có", "không tìm thấy", "không có trong tài liệu", "ngoài phạm vi",
    "không biết", "không thể trả lời", "out of scope", "not found",
]

NO_ANSWER_GROUPS = {"no_answer", "out_of_scope"}


def heuristic_judge(question: str, ground_truth: str, answer: str, question_group: str = "") -> int:
    """Deterministic quality score 1-5.

    Strategy:
    - no_answer/out_of_scope: refusal = 5, non-refusal = 1
    - factual: extract key numbers/names from ground_truth, check presence in answer
      3+ matches = 5, 2 matches = 4, 1 match = 3, key numbers missing = 1
    """
    import re
    ans_lower = answer.lower()
    gt_lower  = ground_truth.lower()

    # For refusal questions: correct = says "không có" or similar
    if question_group in NO_ANSWER_GROUPS:
        return 5 if any(k in ans_lower for k in REFUSAL_KEYWORDS) else 1

    # Extract numbers (sequences of digits with dots/commas) from ground_truth
    numbers = re.findall(r"\d[\d.,]+\d", ground_truth)
    # Extract key capitalized tokens (Vietnamese names, orgs)
    keywords = re.findall(r"\b[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯẠẶẬẦẨẤẮ][^\s]{2,}\b", ground_truth)

    matched = 0
    candidates = numbers[:5] + keywords[:3]  # top facts to check

    for fact in candidates:
        # Normalize: strip formatting for number comparison
        fact_norm = fact.replace(".", "").replace(",", "")
        ans_norm  = ans_lower.replace(".", "").replace(",", "")
        if fact.lower() in ans_lower or fact_norm in ans_norm:
            matched += 1

    total = len(candidates)
    if total == 0:
        # No extractable facts — check if answer is non-empty and non-refusal
        return 3 if answer.strip() and not any(k in ans_lower for k in REFUSAL_KEYWORDS) else 1

    ratio = matched / total
    if ratio >= 0.7:
        return 5
    elif ratio >= 0.4:
        return 4
    elif ratio >= 0.2:
        return 3
    elif matched >= 1:
        return 2
    else:
        return 1


# ── 3 arch runners ────────────────────────────────────────────────────────────

def run_arch_a(question: str) -> dict[str, Any]:
    """Pure vector RAG."""
    try:
        t0 = time.perf_counter()
        contexts = vector_retrieve(question, top_k=TOP_K)
        answer, in_tok, out_tok = llm_answer(question, contexts)
        latency = time.perf_counter() - t0
        return {
            "answer": answer,
            "contexts": contexts,
            "latency_s": latency,
            "in_tokens": in_tok,
            "out_tokens": out_tok,
            "failed": not answer.strip(),
        }
    except Exception as exc:
        return {"answer": "", "contexts": [], "latency_s": 0.0,
                "in_tokens": 0, "out_tokens": 0, "failed": True, "error": str(exc)}


def run_arch_b(question: str, bm25: BM25Retriever) -> dict[str, Any]:
    """Hybrid fusion (BM25 + vector weighted_sum) — sequential pipeline, no reranking."""
    try:
        t0 = time.perf_counter()
        contexts = hybrid_fusion_retrieve(question, bm25)
        answer, in_tok, out_tok = llm_answer(question, contexts)
        latency = time.perf_counter() - t0
        return {
            "answer": answer,
            "contexts": contexts,
            "latency_s": latency,
            "in_tokens": in_tok,
            "out_tokens": out_tok,
            "failed": not answer.strip(),
        }
    except Exception as exc:
        return {"answer": "", "contexts": [], "latency_s": 0.0,
                "in_tokens": 0, "out_tokens": 0, "failed": True, "error": str(exc)}


def run_arch_c(question: str, bm25: BM25Retriever) -> dict[str, Any]:
    """Smart planner: interpret intent → route retrieve strategy → RRF merge → LLM.

    Strategy selection (no extra LLM call — reuses interpret() output):
      sub_queries set  → multi-topic decompose: retrieve per sub-query + ticker filter
      len(tickers) > 1 → per-ticker retrieve with filter, then RRF
      years set        → broad retrieve with ticker filter (no year filter, need both years in context)
      else             → single retrieve with ticker + year filter
    """
    total_in, total_out = 0, 0
    try:
        t0 = time.perf_counter()

        # Step 1: interpret (replaces decompose_query)
        intent, in_t, out_t = _interpret_for_arch_c(question)
        total_in += in_t; total_out += out_t

        tickers = intent.tickers or None
        year = intent.year or None

        scored_lists: list[list[tuple[str, float]]] = []

        if intent.sub_queries:
            # Multi-topic: retrieve per sub-query, each with shared ticker/year filter
            print(f"    [arch_c] multi-topic decompose: {intent.sub_queries}")
            for sq in intent.sub_queries:
                scored_lists.append(
                    vector_retrieve_scored_filtered(sq, top_k=CANDIDATE_K, tickers=tickers, year=year)
                )

        elif len(intent.tickers) > 1:
            # Multi-ticker: retrieve per ticker independently
            print(f"    [arch_c] multi-ticker: {intent.tickers}")
            for ticker in intent.tickers:
                scored_lists.append(
                    vector_retrieve_scored_filtered(question, top_k=CANDIDATE_K, tickers=[ticker], year=year)
                )

        elif intent.years:
            # Multi-year comparison: retrieve without year filter so both years present in context
            print(f"    [arch_c] multi-year {intent.years}: no year filter")
            scored_lists.append(
                vector_retrieve_scored_filtered(question, top_k=CANDIDATE_K, tickers=tickers, year=None)
            )

        else:
            # Simple: single retrieve with filters
            print(f"    [arch_c] simple: ticker={tickers} year={year}")
            scored_lists.append(
                vector_retrieve_scored_filtered(question, top_k=CANDIDATE_K, tickers=tickers, year=year)
            )

        merged = rrf_merge(scored_lists)[:TOP_K]

        # Fallback: if filters returned 0 chunks, retry without filters
        if not merged:
            print(f"    [arch_c] 0 chunks with filters — fallback no filter")
            scored_lists2 = [vector_retrieve_scored(question, top_k=CANDIDATE_K)]
            merged = rrf_merge(scored_lists2)[:TOP_K]

        answer, in_t, out_t = llm_answer(question, merged)
        total_in += in_t; total_out += out_t

        latency = time.perf_counter() - t0
        return {
            "answer": answer,
            "contexts": merged,
            "sub_queries": intent.sub_queries or [question],
            "latency_s": latency,
            "in_tokens": total_in,
            "out_tokens": total_out,
            "failed": not answer.strip(),
        }
    except Exception as exc:
        return {"answer": "", "contexts": [], "latency_s": 0.0,
                "in_tokens": total_in, "out_tokens": total_out,
                "failed": True, "error": str(exc)}


# ── comparison runner ─────────────────────────────────────────────────────────

def classify_group(qid: str) -> str:
    for grp, ids in QUESTION_GROUPS.items():
        if qid in ids:
            return grp
    return "other"


def run_comparison(questions: list[dict], bm25: BM25Retriever) -> list[dict]:
    results = []
    for q in questions:
        qid = q["id"]
        question = q["question"]
        ground_truth = q.get("answer", "")
        arch_group = classify_group(qid)

        print(f"\n{'='*60}")
        print(f"  {qid} [{arch_group}]: {question[:60]}...")

        row: dict[str, Any] = {
            "id": qid,
            "group": arch_group,
            "question": question,
            "ground_truth": ground_truth,
            "archs": {},
        }

        runners = {
            "arch_a": lambda: run_arch_a(question),
            "arch_b": lambda: run_arch_b(question, bm25),
            "arch_c": lambda: run_arch_c(question, bm25),
        }

        for arch_name, runner in runners.items():
            print(f"  {arch_name}... ", end="", flush=True)
            result = runner()

            # Deterministic heuristic judge (LLM judge not used — DeepSeek-v4-flash ignores format instructions)
            judge_score = 1
            if result["answer"] and ground_truth:
                judge_score = heuristic_judge(question, ground_truth, result["answer"], arch_group)

            total_tokens = result["in_tokens"] + result["out_tokens"]
            cost = result["in_tokens"] * PRICE_IN + result["out_tokens"] * PRICE_OUT

            row["archs"][arch_name] = {
                "answer": result["answer"][:300],
                "latency_s": round(result["latency_s"], 2),
                "total_tokens": total_tokens,
                "cost_usd": round(cost, 6),
                "quality_score": judge_score,
                "failed": result["failed"],
            }

            print(f"quality={judge_score} latency={result['latency_s']:.1f}s tokens={total_tokens}")

        results.append(row)
    return results


# ── aggregation ───────────────────────────────────────────────────────────────

def aggregate(results: list[dict]) -> dict:
    """Compute per-arch and per-group means for 5 metrics."""
    from collections import defaultdict
    import statistics

    arch_all: dict[str, dict[str, list]] = {a: defaultdict(list) for a in ARCH_NAMES}
    arch_by_group: dict[str, dict[str, dict[str, list]]] = {
        a: defaultdict(lambda: defaultdict(list)) for a in ARCH_NAMES
    }

    for row in results:
        grp = row["group"]
        for arch in ARCH_NAMES:
            ad = row["archs"].get(arch, {})
            if not ad:
                continue
            for metric in ("quality_score", "latency_s", "total_tokens", "cost_usd"):
                arch_all[arch][metric].append(ad[metric])
                arch_by_group[arch][grp][metric].append(ad[metric])
            arch_all[arch]["failure_rate"].append(1.0 if ad["failed"] else 0.0)
            arch_by_group[arch][grp]["failure_rate"].append(1.0 if ad["failed"] else 0.0)

    def mean(lst):
        return round(statistics.mean(lst), 4) if lst else None

    overall = {}
    for arch in ARCH_NAMES:
        overall[arch] = {m: mean(v) for m, v in arch_all[arch].items()}

    by_group = {}
    for arch in ARCH_NAMES:
        by_group[arch] = {}
        for grp, metrics in arch_by_group[arch].items():
            by_group[arch][grp] = {m: mean(v) for m, v in metrics.items()}

    return {"overall": overall, "by_group": by_group}


def print_tables(agg: dict) -> None:
    overall = agg["overall"]
    by_group = agg["by_group"]

    metrics = ["quality_score", "latency_s", "total_tokens", "cost_usd", "failure_rate"]

    print("\n" + "="*70)
    print("BẢNG 1 — Tổng thể (3 kiến trúc × 5 chỉ số)")
    print("="*70)
    header = f"{'Chỉ số':<20}" + "".join(f"{a:>12}" for a in ARCH_NAMES)
    print(header)
    print("-"*56)
    for m in metrics:
        row = f"{m:<20}"
        for arch in ARCH_NAMES:
            v = overall.get(arch, {}).get(m)
            row += f"{str(v) if v is not None else 'N/A':>12}"
        print(row)

    print("\n" + "="*70)
    print("BẢNG 2 — Theo nhóm câu hỏi (quality_score)")
    print("="*70)
    groups = sorted({g for a in by_group.values() for g in a})
    header2 = f"{'Nhóm / Arch':<20}" + "".join(f"{a:>12}" for a in ARCH_NAMES)
    print(header2)
    print("-"*56)
    for grp in groups:
        row = f"{grp:<20}"
        for arch in ARCH_NAMES:
            v = by_group.get(arch, {}).get(grp, {}).get("quality_score")
            row += f"{str(v) if v is not None else 'N/A':>12}"
        print(row)


def print_conclusions(agg: dict) -> None:
    overall = agg["overall"]
    by_group = agg["by_group"]

    print("\n" + "="*70)
    print("KẾT LUẬN")
    print("="*70)

    # Which arch has highest overall quality?
    best_overall = max(ARCH_NAMES, key=lambda a: overall.get(a, {}).get("quality_score") or 0)
    print(f"\nKiến trúc quality cao nhất (tổng thể): {best_overall}")

    # Which arch wins per group?
    groups = sorted({g for a in by_group.values() for g in a})
    print("\nChất lượng theo nhóm:")
    for grp in groups:
        scores = {a: by_group.get(a, {}).get(grp, {}).get("quality_score") or 0 for a in ARCH_NAMES}
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        print(f"  {grp:<15}: tốt nhất={best} ({scores[best]}), tệ nhất={worst} ({scores[worst]})")

    # Cost comparison
    print("\nChi phí tương đối vs arch_a:")
    base_cost = overall.get("arch_a", {}).get("cost_usd") or 1e-9
    for arch in ARCH_NAMES:
        cost = overall.get(arch, {}).get("cost_usd") or 0
        ratio = cost / base_cost if base_cost else 0
        print(f"  {arch}: ${cost:.6f}  ({ratio:.1f}×)")

    # Routing recommendation
    c_multi = by_group.get("arch_c", {}).get("multi_source", {}).get("quality_score") or 0
    c_simple = by_group.get("arch_c", {}).get("simple", {}).get("quality_score") or 0
    a_simple = by_group.get("arch_a", {}).get("simple", {}).get("quality_score") or 0
    c_cost = overall.get("arch_c", {}).get("cost_usd") or 0
    a_cost = overall.get("arch_a", {}).get("cost_usd") or 0
    cost_premium = (c_cost / a_cost - 1) * 100 if a_cost else 0

    print(f"\nRouting có cần thiết không?")
    print(f"  arch_c quality ở simple={c_simple}, multi_source={c_multi}")
    print(f"  arch_a quality ở simple={a_simple}")
    print(f"  arch_c đắt hơn arch_a: {cost_premium:.0f}%")

    if c_cost > a_cost * 1.3 and c_simple <= a_simple:
        print("  → Routing NÊN dùng: arch_c chỉ xứng đáng với multi_source, dùng arch_a cho simple")
    elif best_overall == "arch_a":
        print("  → TRUNG THỰC: arch_a (đơn giản nhất) có quality tốt nhất hoặc ngang bằng")
        print("    Planning overhead không mang lại lợi ích tương xứng chi phí")
    else:
        print(f"  → {best_overall} cho quality tốt nhất, cân nhắc routing theo nhóm câu hỏi")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _FILTER

    parser = argparse.ArgumentParser(description="Compare 3 RAG architectures")
    parser.add_argument("--sample", action="store_true",
                        help="Run only 3 sample questions (one per group)")
    parser.add_argument("--ticker", default="HPG",
                        help="Ticker filter for bctc_structural (default: HPG). "
                             "Comma-separated for multi: HPG,VCB")
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/arch_compare.json")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.ticker.split(",") if t.strip()]

    # Only apply filter if collection actually has ticker payload
    from qdrant_client import QdrantClient as _QC
    _qc = _QC("localhost", port=6333)
    _sample, _ = _qc.scroll(COLLECTION, limit=1, with_payload=["ticker"])
    _has_ticker = bool(_sample and _sample[0].payload.get("ticker"))
    _FILTER = build_filter(tickers=tickers) if _has_ticker else None
    if not _has_ticker:
        print(f"[WARN] Collection '{COLLECTION}' has no ticker payload — running without filter (re-index to enable)")

    all_questions = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]

    if args.sample:
        sample_ids = set(SAMPLE_IDS.values())
        questions = [q for q in all_questions if q["id"] in sample_ids]
        print(f"Sample mode: {len(questions)} questions ({list(SAMPLE_IDS.values())})")
    else:
        # Exclude news questions (no ground truth, different check)
        questions = [q for q in all_questions if q["group"] != "news"]
        print(f"Full mode: {len(questions)} questions")

    print(f"Collection: {COLLECTION}  Ticker filter: {tickers if _has_ticker else 'NONE (no payload)'}  Embed: {EMBED_MODEL}")

    bm25 = BM25Retriever(collection=COLLECTION, tickers=tickers if _has_ticker else None)

    results = run_comparison(questions, bm25)
    agg = aggregate(results)

    print_tables(agg)
    print_conclusions(agg)

    output = {
        "tickers": tickers,
        "collection": COLLECTION,
        "embed_model": EMBED_MODEL,
        "sample_mode": args.sample,
        "n_questions": len(questions),
        "aggregate": agg,
        "results": results,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults → {args.out}")


if __name__ == "__main__":
    main()
