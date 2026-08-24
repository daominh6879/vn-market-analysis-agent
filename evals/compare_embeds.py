"""
evals/compare_embeds.py — So sánh embedding models cho RAG pipeline.

Index hpg_pymupdf.md với fixed_512 bằng từng model, chạy eval RAG, in bảng so sánh.

Usage:
    python evals/compare_embeds.py
    python evals/compare_embeds.py --models nomic-embed-text bge-m3
    python evals/compare_embeds.py --models nomic-embed-text bge-m3 mxbai-embed-large --skip-ragas
    python evals/compare_embeds.py --out evals/embed_compare.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from pathlib import Path

# ragas VertexAI shim (same fix as run.py)
from langchain_google_vertexai import ChatVertexAI as _ChatVertexAI
from langchain_google_vertexai import VertexAI as _VertexAI
_cv = types.ModuleType("langchain_community.chat_models.vertexai")
_cv.ChatVertexAI = _ChatVertexAI  # type: ignore
sys.modules.setdefault("langchain_community.chat_models.vertexai", _cv)
_lv = types.ModuleType("langchain_community.llms.vertexai")
_lv.VertexAI = _VertexAI  # type: ignore
sys.modules.setdefault("langchain_community.llms.vertexai", _lv)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from qdrant_client import QdrantClient
from rag.chunking import chunk_fixed
from rag.index import embed_batch, recreate_collection, index_chunks, get_embed_dim
from evals.run import (
    load_questions, ask_with_rag, is_refusal, compute_ragas,
    METRIC_DISPLAY,
)
from llm.factory import create_client

DEFAULT_MODELS = ["nomic-embed-text", "bge-m3", "mxbai-embed-large"]
INPUT_MD = ROOT / "outputs" / "2025" / "hpg_pymupdf.md"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def safe_name(model: str) -> str:
    return model.replace(":", "_").replace("/", "_").replace("-", "_")


def index_for_model(
    text: str,
    embed_model: str,
    qdrant: QdrantClient,
) -> tuple[str, int, float]:
    """Index text with fixed_512 for given embed model. Returns (collection, n_chunks, secs)."""
    collection = f"hpg_emb_{safe_name(embed_model)}"
    chunks = chunk_fixed(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    t0 = time.perf_counter()
    dim = get_embed_dim(embed_model)
    recreate_collection(qdrant, collection, dim)
    index_chunks(qdrant, collection, chunks, embed_model, doc_id="eval", meta=None)
    elapsed = time.perf_counter() - t0
    return collection, len(chunks), elapsed


def eval_model(
    embed_model: str,
    collection: str,
    questions: list[dict],
    llm_client,
    ragas_provider: str,
    ollama_judge: str,
    ollama_embed_model: str,
    skip_ragas: bool,
) -> dict:
    eval_qs = [q for q in questions if q["group"] not in ("no_answer", "out_of_scope")]
    refusal_qs = [q for q in questions if q["group"] in ("no_answer", "out_of_scope")]

    samples = []
    for q in eval_qs:
        print(f"    {q['id']:6s} [{q['group']:<20}]", end="", flush=True)
        t0 = time.perf_counter()
        answer, contexts = ask_with_rag(llm_client, q["question"], collection, embed_model)
        print(f" {time.perf_counter()-t0:5.1f}s")
        samples.append({
            "id": q["id"],
            "group": q["group"],
            "question": q["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": q["answer"],
        })

    refusal_results = []
    for q in refusal_qs:
        print(f"    {q['id']:6s} [{q['group']:<20}]", end="", flush=True)
        t0 = time.perf_counter()
        answer, _ = ask_with_rag(llm_client, q["question"], collection, embed_model)
        passed = is_refusal(answer)
        print(f" {'PASS' if passed else 'FAIL'} {time.perf_counter()-t0:5.1f}s")
        refusal_results.append({"id": q["id"], "passed": passed})

    refusal_rate = sum(r["passed"] for r in refusal_results) / len(refusal_results) if refusal_results else 1.0

    ragas_scores: dict = {}
    if not skip_ragas and samples:
        print(f"  → computing RAGAS ({ragas_provider})...")
        ragas_scores = compute_ragas(samples, ragas_provider, ollama_judge, ollama_embed_model)

    return {**ragas_scores, "refusal_pass_rate": refusal_rate}


def print_comparison(results: dict[str, dict]) -> None:
    metrics = []
    for scores in results.values():
        for k in scores:
            if k not in metrics:
                metrics.append(k)

    col_w = max(len(m) for m in results) + 2
    label_w = 40

    print("\n## Embedding Model Comparison\n")
    header = f"| {'Metric':<{label_w}} |"
    sep    = f"| {'-'*label_w} |"
    for model in results:
        header += f" {model:>{col_w}} |"
        sep    += f" {'-'*col_w} |"
    print(header)
    print(sep)

    for metric in metrics:
        label = METRIC_DISPLAY.get(metric, metric)
        row = f"| {label:<{label_w}} |"
        vals = [results[m].get(metric) for m in results]
        best_val = max((v for v in vals if v is not None), default=None)
        for model, val in zip(results, vals):
            if val is None:
                row += f" {'—':>{col_w}} |"
            elif isinstance(val, float):
                marker = " *" if (best_val is not None and abs(val - best_val) < 1e-9) else "  "
                row += f" {val:>{col_w-2}.3f}{marker}|"
            else:
                row += f" {str(val):>{col_w}} |"
        print(row)
    print("\n* = best in row")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare embedding models for RAG")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="Ollama embedding models to compare")
    parser.add_argument("--input", default=str(INPUT_MD), help="Parsed markdown file")
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/embed_compare.json")
    parser.add_argument("--skip-ragas", action="store_true")
    parser.add_argument("--ragas-provider",
                        default=os.environ.get("RAGAS_PROVIDER", "deepseek"),
                        choices=["ollama", "anthropic", "openai", "deepseek"])
    parser.add_argument("--ollama-model",
                        default=os.environ.get("OLLAMA_MODEL", "qwen3:8b"))
    parser.add_argument("--ollama-embed-model",
                        default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                        help="Embed model for RAGAS answer_relevancy scoring")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    questions = load_questions(Path(args.questions))
    qdrant = QdrantClient("localhost", port=6333)
    llm_client = create_client()

    print(f"Input       : {args.input}  ({len(text):,} chars)")
    print(f"Models      : {args.models}")
    print(f"RAGAS judge : {args.ragas_provider}")
    print(f"Questions   : {len(questions)}\n")

    all_results: dict[str, dict] = {}
    index_stats: dict[str, dict] = {}

    # ── Phase 1: index all models ─────────────────────────────────────────────
    print("=== Phase 1: Indexing ===")
    collections: dict[str, str] = {}
    for model in args.models:
        print(f"\n[{model}] indexing...")
        collection, n_chunks, elapsed = index_for_model(text, model, qdrant)
        collections[model] = collection
        index_stats[model] = {"collection": collection, "n_chunks": n_chunks, "index_secs": elapsed}
        print(f"  → {n_chunks} chunks in {elapsed:.1f}s")

    # ── Phase 2: eval each model ──────────────────────────────────────────────
    print("\n=== Phase 2: Eval ===")
    for model in args.models:
        print(f"\n[{model}]")
        scores = eval_model(
            embed_model=model,
            collection=collections[model],
            questions=questions,
            llm_client=llm_client,
            ragas_provider=args.ragas_provider,
            ollama_judge=args.ollama_model,
            ollama_embed_model=args.ollama_embed_model,
            skip_ragas=args.skip_ragas,
        )
        all_results[model] = scores
        print(f"  refusal_pass_rate = {scores['refusal_pass_rate']:.3f}")

    # ── Print comparison ──────────────────────────────────────────────────────
    print_comparison(all_results)

    # ── Index stats ───────────────────────────────────────────────────────────
    print("\n## Index Stats\n")
    print(f"| {'Model':<30} | {'Dims':>6} | {'Chunks':>7} | {'Index time':>11} |")
    print(f"| {'-'*30} | {'-'*6} | {'-'*7} | {'-'*11} |")
    for model, stat in index_stats.items():
        try:
            import httpx
            r = httpx.post("http://localhost:11434/api/embeddings",
                           json={"model": model, "prompt": "test"}, timeout=30)
            dims = len(r.json()["embedding"])
        except Exception:
            dims = "?"
        print(f"| {model:<30} | {dims:>6} | {stat['n_chunks']:>7} | {stat['index_secs']:>10.1f}s |")

    # ── Save ──────────────────────────────────────────────────────────────────
    output = {
        "models": args.models,
        "results": all_results,
        "index_stats": index_stats,
    }
    Path(args.out).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nResults → {args.out}")

    # ── Winner ────────────────────────────────────────────────────────────────
    ragas_keys = ["faithfulness", "answer_relevancy", "response_relevancy",
                  "context_precision", "context_recall"]
    def avg_score(model_scores: dict) -> float:
        vals = [model_scores[k] for k in ragas_keys if k in model_scores and isinstance(model_scores[k], float)]
        return sum(vals) / len(vals) if vals else 0.0

    if not args.skip_ragas:
        ranked = sorted(all_results.items(), key=lambda x: avg_score(x[1]), reverse=True)
        print(f"\nAverage RAGAS ranking:")
        for i, (model, scores) in enumerate(ranked, 1):
            print(f"  {i}. {model:<30} avg={avg_score(scores):.3f}")
        print(f"\n→ Winner: {ranked[0][0]}")


if __name__ == "__main__":
    main()
