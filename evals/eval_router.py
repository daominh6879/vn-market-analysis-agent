#!/usr/bin/env python3
"""
evals/eval_router.py — Measure router classification accuracy on 30 test questions.

Usage:
    python evals/eval_router.py
    python evals/eval_router.py --questions evals/router_test.yaml --out evals/router_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from llm.factory import create_client
from rag.router import classify


def main() -> None:
    parser = argparse.ArgumentParser(description="Router accuracy eval")
    parser.add_argument("--questions", default="evals/router_test.yaml")
    parser.add_argument("--out", default="evals/router_eval.json")
    args = parser.parse_args()

    data = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))
    questions = data["questions"]
    client = create_client()

    results = []
    correct = 0

    for q in questions:
        print(f"  {q['id']:5s}  ", end="", flush=True)
        result = classify(q["question"], client=client)
        match = result.label == q["expected"]
        correct += int(match)
        mark = "OK" if match else "FAIL"
        print(f"[{mark}] expected={q['expected']:<16} got={result.label:<16} | {result.reason[:60]}")
        results.append({
            "id": q["id"],
            "question": q["question"],
            "expected": q["expected"],
            "got": result.label,
            "correct": match,
            "reason": result.reason,
        })

    accuracy = correct / len(questions)
    print(f"\nAccuracy: {correct}/{len(questions)} = {accuracy:.1%}")
    if accuracy >= 0.9:
        print("PASS (≥ 90%)")
    else:
        wrong = [r for r in results if not r["correct"]]
        print(f"FAIL — {len(wrong)} wrong:")
        for r in wrong:
            print(f"  {r['id']}: expected={r['expected']} got={r['got']}")

    Path(args.out).write_text(
        json.dumps({"accuracy": accuracy, "correct": correct, "total": len(questions), "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved → {args.out}")


if __name__ == "__main__":
    main()
