"""
evals/compare_chunking.py — Bài 7 re-run: so sánh 6 collection (3 strategy × 2 metadata).

Chạy:
    python evals/compare_chunking.py
    python evals/compare_chunking.py --full-ragas   # thêm 4 RAGAS metrics (chậm)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types
import time
from pathlib import Path

from langchain_google_vertexai import ChatVertexAI as _ChatVertexAI
from langchain_google_vertexai import VertexAI as _VertexAI
_cv = types.ModuleType("langchain_community.chat_models.vertexai")
_cv.ChatVertexAI = _ChatVertexAI  # type: ignore
sys.modules.setdefault("langchain_community.chat_models.vertexai", _cv)
_lv = types.ModuleType("langchain_community.llms.vertexai")
_lv.VertexAI = _VertexAI  # type: ignore
sys.modules.setdefault("langchain_community.llms.vertexai", _lv)

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from evals.run import (
    load_questions, ask_with_rag, ask_baseline, is_refusal,
    compute_ragas, REFUSAL_KEYWORDS,
)
from llm.factory import create_client

GOLDEN = ROOT / "evals" / "golden_hpg.yaml"
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")

COLLECTIONS = [
    {"name": "hpg_b7_fixed_nometa",      "label": "fixed_512    | no meta"},
    {"name": "hpg_b7_fixed_meta",        "label": "fixed_512    | meta   "},
    {"name": "hpg_b7_structural_nometa", "label": "structural   | no meta"},
    {"name": "hpg_b7_structural_meta",   "label": "structural   | meta   "},
    {"name": "hpg_b7_hier_nometa",       "label": "hierarchical | no meta"},
    {"name": "hpg_b7_hier_meta",         "label": "hierarchical | meta   "},
]


def eval_collection(
    name: str,
    questions: list[dict],
    refusal_qs: list[dict],
    client,
    full_ragas: bool,
    ragas_provider: str,
    ollama_model: str,
) -> dict:
    samples = []
    for q in questions:
        answer, contexts = ask_with_rag(client, q["question"], name, EMBED_MODEL)
        samples.append({
            "question": q["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": q["answer"],
        })

    refusal_passed = 0
    for q in refusal_qs:
        answer, _ = ask_with_rag(client, q["question"], name, EMBED_MODEL)
        if is_refusal(answer):
            refusal_passed += 1
    refusal_rate = refusal_passed / len(refusal_qs) if refusal_qs else 1.0

    scores: dict = {"refusal_pass_rate": refusal_rate}
    if full_ragas and samples:
        ragas = compute_ragas(samples, ragas_provider, ollama_model, EMBED_MODEL)
        scores.update(ragas)
    return scores


def print_table(results: list[dict]) -> None:
    metrics = list(results[0]["scores"].keys())
    col_w = 14

    header = f"{'Collection':<35} " + " ".join(f"{m[:col_w]:>{col_w}}" for m in metrics)
    sep    = "-" * len(header)
    print("\n## Bài 7 Re-run — 3 strategies × metadata (2024 + 2025)\n")
    print(sep)
    print(header)
    print(sep)
    for r in results:
        row = f"{r['label']:<35} " + " ".join(
            f"{r['scores'].get(m, 0.0):>{col_w}.3f}" for m in metrics
        )
        print(row)
    print(sep)
    print()

    # highlight best per metric
    print("Best per metric:")
    for m in metrics:
        best = max(results, key=lambda r: r["scores"].get(m, 0.0))
        print(f"  {m:<25} → {best['label'].strip()} ({best['scores'].get(m, 0.0):.3f})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-ragas", action="store_true",
                        help="Also compute 4 RAGAS metrics (chậm)")
    parser.add_argument("--ragas-provider",
                        default=os.environ.get("RAGAS_PROVIDER", "deepseek"),
                        choices=["ollama", "anthropic", "openai", "deepseek"])
    parser.add_argument("--ollama-model",
                        default=os.environ.get("OLLAMA_MODEL", "qwen3:8b"))
    args = parser.parse_args()

    questions_all = load_questions(GOLDEN)
    eval_qs    = [q for q in questions_all
                  if q["group"] not in ("no_answer", "out_of_scope")
                  and q.get("indexed", True)]
    refusal_qs = [q for q in questions_all
                  if q["group"] in ("no_answer", "out_of_scope")]

    print(f"Eval questions  : {len(eval_qs)} (indexed=true)")
    print(f"Refusal questions: {len(refusal_qs)}")
    print(f"Embed model     : {EMBED_MODEL}")
    print(f"RAGAS           : {'YES — ' + args.ragas_provider if args.full_ragas else 'NO (--full-ragas để bật)'}")
    print()

    client = create_client()
    results = []
    for col in COLLECTIONS:
        print(f"── {col['label']} ({col['name']}) ──")
        t0 = time.perf_counter()
        scores = eval_collection(
            col["name"], eval_qs, refusal_qs, client,
            args.full_ragas, args.ragas_provider, args.ollama_model,
        )
        elapsed = time.perf_counter() - t0
        print(f"   done in {elapsed:.0f}s  refusal={scores['refusal_pass_rate']:.3f}")
        results.append({"label": col["label"], "name": col["name"], "scores": scores})

    print_table(results)

    out = ROOT / "evals" / "chunking_compare_b7_rerun.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results → {out}")


if __name__ == "__main__":
    main()
