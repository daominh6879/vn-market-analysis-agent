"""
evals/eval_memory_b30.py — Bài 30: Đo memory recall + precision với real LLM.

Usage:
    python evals/eval_memory_b30.py
    python evals/eval_memory_b30.py --yaml evals/memory_multi_session.yaml
    python evals/eval_memory_b30.py --update-notes

Metrics:
    recall    = fraction of scenarios where all expected_remembered terms appear in conv3 reply
    precision = fraction of scenarios where none of expected_NOT_mentioned appear in conv3 reply

Each scenario uses a fresh unique user_id to prevent cross-scenario contamination.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from memory.conversation import create_conversation
from memory.turn_handler import finish_conversation, run_turn


def _run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    sid = scenario["id"]
    desc = scenario["description"]
    convs = scenario["conversations"]
    expected_remembered: list[str] = scenario.get("expected_remembered", [])
    expected_not: list[str] = scenario.get("expected_NOT_mentioned", [])

    # Fresh user per scenario — prevents state bleed between scenarios
    user_id = f"eval_b30_{sid}_{uuid.uuid4().hex[:8]}"
    tenant_id = "eval_b30"

    print(f"\n[{sid}] {desc}")
    print(f"  user_id={user_id}")

    conv3_reply = ""
    conv_ids: list[str] = []

    for conv_idx, conv in enumerate(convs):
        cid = create_conversation(user_id, tenant_id)
        conv_ids.append(cid)
        is_last = conv_idx == len(convs) - 1

        if is_last:
            # Conv 3: single query, capture reply
            query: str = conv.get("query", conv.get("turns", [""])[0])
            is_first = len(convs) == 1  # only first if it's the very first conv
            reply = run_turn(
                conversation_id=cid,
                user_id=user_id,
                user_message=query,
                tenant_id=tenant_id,
                is_first_turn=True,
            )
            conv3_reply = reply
            print(f"  Conv3 reply (first 200 chars): {reply[:200]!r}")
        else:
            # Conv 1 & 2: run turns to build up memory
            turns: list[str] = conv.get("turns", [])
            for t_idx, msg in enumerate(turns):
                is_first_turn = t_idx == 0
                run_turn(
                    conversation_id=cid,
                    user_id=user_id,
                    user_message=msg,
                    tenant_id=tenant_id,
                    is_first_turn=is_first_turn,
                )
            # Summarise conversation into episodic memory
            summary = f"Người dùng đã hỏi: {turns[0][:100] if turns else ''}"
            conclusion = "Đã ghi nhận sở thích/câu hỏi của người dùng."
            finish_conversation(
                conversation_id=cid,
                user_id=user_id,
                first_question=turns[0] if turns else "",
                summary=summary,
                conclusion=conclusion,
            )

    # Evaluate
    recall_pass = True
    precision_pass = True
    recall_detail: list[str] = []
    precision_detail: list[str] = []

    reply_lower = conv3_reply.lower()

    for term in expected_remembered:
        found = term.lower() in reply_lower
        if not found:
            recall_pass = False
            recall_detail.append(f"MISSING: {term!r}")
        else:
            recall_detail.append(f"FOUND: {term!r}")

    for phrase in expected_not:
        found = phrase.lower() in reply_lower
        if found:
            precision_pass = False
            precision_detail.append(f"HALLUCINATED: {phrase!r}")
        else:
            precision_detail.append(f"ABSENT(ok): {phrase!r}")

    status_recall = "PASS" if (recall_pass or not expected_remembered) else "FAIL"
    status_prec = "PASS" if precision_pass else "FAIL"
    print(f"  Recall: {status_recall} {recall_detail}")
    print(f"  Precision: {status_prec} {precision_detail}")

    return {
        "id": sid,
        "recall_pass": recall_pass or not expected_remembered,
        "precision_pass": precision_pass,
        "recall_detail": recall_detail,
        "precision_detail": precision_detail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bài 30 memory eval")
    parser.add_argument("--yaml", default="evals/memory_multi_session.yaml")
    parser.add_argument("--update-notes", action="store_true",
                        help="Append results to docs/notes/bai-30-memory-isolation.md")
    args = parser.parse_args()

    yaml_path = ROOT / args.yaml
    with open(yaml_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    scenarios: list[dict] = config["scenarios"]
    results: list[dict] = []

    for scenario in scenarios:
        try:
            result = _run_scenario(scenario)
        except Exception as exc:
            print(f"  ERROR in {scenario['id']}: {exc}")
            result = {
                "id": scenario["id"],
                "recall_pass": False,
                "precision_pass": False,
                "recall_detail": [f"ERROR: {exc}"],
                "precision_detail": [],
            }
        results.append(result)

    total = len(results)
    recall_score = sum(1 for r in results if r["recall_pass"]) / total * 100
    precision_score = sum(1 for r in results if r["precision_pass"]) / total * 100

    print("\n" + "=" * 60)
    print(f"memory_recall    = {recall_score:.0f}%  ({sum(1 for r in results if r['recall_pass'])}/{total})")
    print(f"memory_precision = {precision_score:.0f}%  ({sum(1 for r in results if r['precision_pass'])}/{total})")
    print("=" * 60)

    if args.update_notes:
        _append_to_notes(recall_score, precision_score, results)


def _append_to_notes(recall: float, precision: float, results: list[dict]) -> None:
    notes_path = ROOT / "docs/notes/bai-30-memory-isolation.md"
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = "\n".join(
        f"| {r['id']} | {'✅' if r['recall_pass'] else '❌'} | {'✅' if r['precision_pass'] else '❌'} |"
        for r in results
    )
    block = f"""
## Eval run {ts}

| Scenario | Recall | Precision |
|----------|--------|-----------|
{rows}

**memory_recall = {recall:.0f}%**
**memory_precision = {precision:.0f}%**
"""
    with open(notes_path, "a", encoding="utf-8") as f:
        f.write(block)
    print(f"Appended to {notes_path}")


if __name__ == "__main__":
    main()
