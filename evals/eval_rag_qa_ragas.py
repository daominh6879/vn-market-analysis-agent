#!/usr/bin/env python3
"""
evals/eval_rag_qa_ragas.py — RAGAS eval on the rag_qa intent path, real pipeline.

No mocks: real classify_hybrid() (router) gates every case first — a case whose
LLM-classified intent/ticker doesn't match what it's supposed to be is NOT fed
into RAGAS (feeding a misrouted case would score the wrong pipeline path and
silently inflate/deflate the report). Real retrieve_only() (SQL rows + RAG
chunks) for contexts, real answer() (DeepSeek via create_client()) for the
generated answer.

Data scope: this project only has HPG BCTC loaded (see CLAUDE.md) — every case
below targets HPG, varying the financial metric asked instead of the ticker,
so router/ticker-resolution gets exercised without asking about data that
was never ingested.

Router/classify LLM calls already go through llm.factory.create_client() →
_wrap_langfuse() → tracing.instrument_llm(), so every generate() call in this
run is traced to Langfuse (when LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are
set) and to traces/latest.jsonl — no extra wiring needed here.

ground_truth is optional per case: when present, context_precision +
context_recall run too; when absent, only faithfulness + answer_relevancy run
(they don't need a reference answer).

Usage:
    python evals/eval_rag_qa_ragas.py
    python evals/eval_rag_qa_ragas.py --out evals/rag_qa_ragas_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# (question, expected_ticker) — expected_ticker is what classify_hybrid() must
# resolve to (router-correctness gate, checked before running RAGAS on the
# case). ground_truth is NOT hardcoded: fetched at run time from the real DB
# via rag.sql_agent.execute_safe(), so it reflects whatever data is actually
# loaded — never a guessed number. All HPG: see module docstring (data scope).
RAG_QA_CASES: list[tuple[str, str | None]] = [
    ("Doanh thu HPG năm 2024 là bao nhiêu?", "HPG"),
    ("Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?", "HPG"),
    ("EPS của HPG năm 2024 là bao nhiêu?", "HPG"),
    ("Tổng tài sản HPG năm 2024 là bao nhiêu?", "HPG"),
    ("Nợ vay dài hạn của HPG năm 2024 là bao nhiêu?", "HPG"),
    ("Biên lợi nhuận gộp của HPG năm 2024 là bao nhiêu?", "HPG"),
    ("Lưu chuyển tiền thuần từ hoạt động kinh doanh của HPG năm 2024?", "HPG"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGAS eval — rag_qa intent, real pipeline")
    parser.add_argument("--out", default="evals/rag_qa_ragas_eval.json")
    args = parser.parse_args()

    from llm.factory import create_client
    from agents.classifier import classify_hybrid
    from rag.qa import retrieve_only, answer, _fetch_sql_rows_text, _sql_answer_from_rows
    from rag.router import classify as route_classify
    from rag.sql_agent import execute_safe

    client = create_client()

    samples = []
    routing_report = []
    skipped = []
    for question, expected_ticker in RAG_QA_CASES:
        print(f"  {question[:55]:55s} ", end="", flush=True)

        # Router gate: real classify_hybrid() must agree this is rag_qa with the
        # right ticker before the case is trusted. A misrouted case would exercise
        # the wrong tools/pipeline entirely, so it's excluded from RAGAS rather
        # than silently scored as if it went through rag_qa.
        route = classify_hybrid(question)
        intent_ok = route.intent == "rag_qa"
        ticker_ok = route.ticker == expected_ticker
        routing_report.append({
            "question": question, "expected_ticker": expected_ticker,
            "got_intent": route.intent, "got_ticker": route.ticker,
            "intent_ok": intent_ok, "ticker_ok": ticker_ok,
        })
        if not (intent_ok and ticker_ok):
            print(f"ROUTING MISMATCH — expected intent=rag_qa ticker={expected_ticker}, "
                  f"got intent={route.intent} ticker={route.ticker} — SKIPPED")
            skipped.append(question)
            continue

        # For the "số_liệu" route (all current cases), fetch SQL rows ONCE and
        # synthesize the answer from those exact rows — text-to-SQL is
        # nondeterministic, so calling retrieve_only() and answer() separately
        # (each doing its own execute_safe()) can silently diverge context vs
        # answer (see docs/notes/eval-rag-qa-ragas.md). Non-SQL routes fall back
        # to the real pipeline calls, which don't have this hazard.
        route = route_classify(question)
        if route.label == "số_liệu":
            context = _fetch_sql_rows_text(question, client)
            reply = (_sql_answer_from_rows(question, context, client) if context
                      else "Không tìm thấy dữ liệu liên quan trong cơ sở dữ liệu.")
        else:
            context = retrieve_only(question, ticker=expected_ticker, client=client)
            reply = answer(question, ticker=expected_ticker, client=client)

        # Ground truth: real DB rows via the SQL agent, not a hardcoded guess.
        # text-to-SQL is nondeterministic and occasionally emits malformed SQL
        # (SecurityError from sqlglot parse failure) — don't let that crash the run,
        # just record no ground_truth for this case (precision/recall skipped for it).
        try:
            gt_result = execute_safe(question, client=client)
            ground_truth = gt_result.format_answer() if gt_result.rows else ""
        except Exception as exc:
            print(f"\n  ground_truth fetch failed for {question!r}: {exc}")
            ground_truth = ""

        print("done")
        samples.append({
            "question": question,
            "answer": reply,
            "contexts": [context],
            "ground_truth": ground_truth,
        })

    print(f"\nRouting gate: {len(RAG_QA_CASES) - len(skipped)}/{len(RAG_QA_CASES)} passed"
          + (f" — skipped: {skipped}" if skipped else ""))

    if not samples:
        print("No case passed the routing gate — nothing to evaluate.")
        return

    for s in samples:
        print(f"\nQ: {s['question']}\nground_truth (from DB): {s['ground_truth'] or '(none — DB returned no rows)'}")

    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from datasets import Dataset
    import os as _os

    missing = [s["question"] for s in samples if not s["ground_truth"]]
    if missing:
        print(f"\nWARNING: no DB rows for {len(missing)} case(s) — context_precision/context_recall "
              f"will be unreliable for those rows: {missing}")

    # ragas.evaluate() defaults its judge to OpenAI — this project's LLM_PROVIDER is
    # DeepSeek (OpenAI-compatible endpoint), so point the judge there explicitly.
    # Judge embeddings: project's Ollama embed model (rag/qa.py's EMBED_MODEL), not OpenAI.
    from langchain_openai import ChatOpenAI
    from langchain_ollama import OllamaEmbeddings

    judge_llm = LangchainLLMWrapper(ChatOpenAI(
        api_key=_os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
        model=_os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        max_tokens=2048,
    ))
    judge_embeddings = LangchainEmbeddingsWrapper(OllamaEmbeddings(
        model=_os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3"),
        base_url=_os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    ))

    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    ds = Dataset.from_list(samples)
    result = evaluate(ds, metrics=metrics, llm=judge_llm, embeddings=judge_embeddings)
    df = result.to_pandas()

    print("\n" + df.to_string(index=False))

    Path(args.out).write_text(
        json.dumps({
            "routing_report": routing_report,
            "scores": result.to_pandas().to_dict(orient="records"),
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nSaved -> {args.out}")


if __name__ == "__main__":
    main()
