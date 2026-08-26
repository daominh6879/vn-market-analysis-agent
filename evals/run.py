#!/usr/bin/env python3
"""
evals/run.py — Eval runner for RAG pipeline.

Usage:
    python evals/run.py                         # compare against baseline
    python evals/run.py --save-baseline         # set current run as baseline
    python evals/run.py --skip-ragas            # model calls only, no scoring
    python evals/run.py --questions evals/golden.yaml --out evals/results.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types
import time
from pathlib import Path

# ragas 0.4.x hard-imports ChatVertexAI/VertexAI from removed langchain_community paths;
# redirect to the official replacement package langchain-google-vertexai.
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

# Load .env from project root (if python-dotenv available)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from llm.factory import create_client
from llm.types import Message

# ── constants ────────────────────────────────────────────────────────────────

THRESHOLD_DROP = 0.1789  # 2×std measured by measure_noise.py  # 2×std measured by measure_noise.py  # 2×std measured by measure_noise.py  # fail CI if any metric drops > 5 points vs baseline
REFUSAL_KEYWORDS = [
    "không có", "không tìm thấy", "không có trong tài liệu", "ngoài phạm vi",
    "không biết", "không thể trả lời", "out of scope", "not found",
    "i don't know", "cannot find", "no information",
]
METRIC_DISPLAY = {
    "faithfulness": "faithfulness         (có bịa không)",
    "response_relevancy": "answer_relevancy     (đúng câu hỏi không)",
    "answer_relevancy": "answer_relevancy     (đúng câu hỏi không)",
    "context_precision": "context_precision    (xếp hạng tốt không)",
    "context_recall": "context_recall       (ngữ cảnh đủ không)",
    "refusal_pass_rate": "refusal_pass_rate    (từ chối đúng không)",
}

# ── helpers ──────────────────────────────────────────────────────────────────

def load_questions(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]


def retrieve_context(question: str, collection: str, embed_model: str, top_k: int = 5) -> list[str]:
    """Retrieve top-k chunks from Qdrant for a question."""
    import httpx
    from qdrant_client import QdrantClient

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    r = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": embed_model, "prompt": question},
        timeout=30,
    )
    r.raise_for_status()
    qvec = r.json()["embedding"]

    qdrant = QdrantClient("localhost", port=6333)
    results = qdrant.query_points(collection_name=collection, query=qvec, limit=top_k).points
    return [r.payload["text"] for r in results]


def retrieve_context_scored(question: str, collection: str, embed_model: str,
                            top_k: int = 20) -> list[tuple[str, float]]:
    """Retrieve top-k chunks with cosine scores — for hybrid fusion."""
    import httpx
    from qdrant_client import QdrantClient

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    r = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": embed_model, "prompt": question},
        timeout=30,
    )
    r.raise_for_status()
    qvec = r.json()["embedding"]

    qdrant = QdrantClient("localhost", port=6333)
    points = qdrant.query_points(collection_name=collection, query=qvec, limit=top_k).points
    return [(p.payload["text"], float(p.score)) for p in points]


def ask_with_hybrid(
    client,
    question: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
    strategy: str,
    top_k: int = 5,
    candidate_k: int = 20,
) -> tuple[str, list[str]]:
    """Retrieve from BM25 + vector, fuse, then call model."""
    from rag.fusion import weighted_sum_fusion, rrf_fusion

    bm25_scored = bm25_retriever.search_scored(question, top_k=candidate_k)
    vec_scored  = retrieve_context_scored(question, collection, embed_model, top_k=candidate_k)

    if strategy == "hybrid_weighted":
        fused_texts = weighted_sum_fusion(bm25_scored, vec_scored)
    else:
        fused_texts = rrf_fusion(bm25_scored, vec_scored)

    contexts = fused_texts[:top_k]
    context_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'.\n\n"
        f"TÀI LIỆU:\n{context_block}"
    )
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text, contexts


def ask_with_hybrid_rerank(
    client,
    question: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
    candidate_k: int = 20,
    top_k: int = 5,
) -> tuple[str, list[str]]:
    """Retrieve via weighted_sum fusion (top candidate_k) → CrossEncoder rerank → top_k.

    Uses lost-in-middle ordering: highest-scored chunk placed last in context.
    """
    from rag.fusion import weighted_sum_fusion
    from rag.reranker import rerank_for_llm

    bm25_scored = bm25_retriever.search_scored(question, top_k=candidate_k)
    vec_scored  = retrieve_context_scored(question, collection, embed_model, top_k=candidate_k)
    fused       = weighted_sum_fusion(bm25_scored, vec_scored)
    contexts    = rerank_for_llm(question, fused[:candidate_k], top_k=top_k)

    context_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'.\n\n"
        f"TÀI LIỆU:\n{context_block}"
    )
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text, contexts


def ask_with_rag_fusion(
    question: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
    n_sub_queries: int = 4,
    top_k: int = 5,
) -> tuple[str, list[str]]:
    """Full RAG-Fusion pipeline: decompose → multi-retrieve → RRF → analyze."""
    from rag.rag_fusion_graph import run_rag_fusion
    result = run_rag_fusion(
        query=question,
        collection=collection,
        embed_model=embed_model,
        bm25_retriever=bm25_retriever,
        top_k=top_k,
        n_sub_queries=n_sub_queries,
    )
    report = result.get("report", result.get("analysis", ""))
    contexts = result.get("fused_chunks", [])
    return report, contexts


def ask_with_router_sql(
    client,
    question: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
) -> tuple[str, list[str]]:
    """Route question → SQL agent (số_liệu) or hybrid_rerank (diễn_giải/cả_hai) or refusal."""
    from rag.router import classify
    from rag.sql_agent import execute_safe, SecurityError, SQLAgentError

    route = classify(question, client=client)

    if route.label == "ngoài_phạm_vi":
        return "Câu hỏi này nằm ngoài phạm vi của hệ thống tài chính HPG.", []

    if route.label == "số_liệu":
        try:
            result = execute_safe(question, client=client)
            return result.format_answer(), [result.as_context()]
        except (SecurityError, SQLAgentError) as exc:
            return f"Không thể thực thi SQL: {exc}", []

    if route.label == "diễn_giải":
        return ask_with_hybrid_rerank(client, question, collection, embed_model, bm25_retriever)

    # cả_hai: SQL result + RAG context, then LLM combines
    sql_context = ""
    try:
        sql_result = execute_safe(question, client=client)
        sql_context = sql_result.as_context()
    except (SecurityError, SQLAgentError):
        pass

    rag_answer, rag_contexts = ask_with_hybrid_rerank(
        client, question, collection, embed_model, bm25_retriever
    )
    contexts = ([sql_context] if sql_context else []) + rag_contexts
    context_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào dữ liệu và tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có, nói rõ 'Không có trong tài liệu'.\n\n"
        f"DỮ LIỆU:\n{context_block}"
    )
    resp = client.generate([Message(role="user", content=question)], max_tokens=512, system=system)
    return resp.text, contexts


def ask_with_bm25(client, question: str, bm25_retriever) -> tuple[str, list[str]]:
    """Retrieve context via BM25 then call model."""
    contexts = bm25_retriever.search(question, top_k=5)
    context_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'.\n\n"
        f"TÀI LIỆU:\n{context_block}"
    )
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text, contexts


def ask_with_rag(client, question: str, collection: str, embed_model: str) -> tuple[str, list[str]]:
    """Retrieve context from Qdrant then call model."""
    contexts = retrieve_context(question, collection, embed_model)
    context_block = "\n\n---\n\n".join(contexts)
    system = (
        "Bạn là trợ lý tài chính. Dựa vào các đoạn tài liệu dưới đây để trả lời. "
        "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'.\n\n"
        f"TÀI LIỆU:\n{context_block}"
    )
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text, contexts


def ask_baseline(client, question: str) -> tuple[str, list[str]]:
    """Call model with no RAG context — pure knowledge answer."""
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system="Bạn là trợ lý tài chính. Trả lời ngắn gọn và chính xác. "
               "Nếu không có thông tin, nói rõ 'Không có trong tài liệu'.",
    )
    return resp.text, []


def is_refusal(answer: str) -> bool:
    a = answer.lower()
    return any(k in a for k in REFUSAL_KEYWORDS)


# ── RAGAS ────────────────────────────────────────────────────────────────────

def _make_ragas_llm(ragas_provider: str, ollama_model: str, ollama_embed_model: str):
    if ragas_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        from langchain_openai import OpenAIEmbeddings
        chat = ChatAnthropic(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            temperature=0,
        )
        emb = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    elif ragas_provider == "openai":
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        chat = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            temperature=0,
        )
        emb = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    elif ragas_provider == "deepseek":
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        class _DeepSeekChat(ChatOpenAI):
            """Force n=1 — DeepSeek rejects n>1 even when RAGAS passes it at call time."""
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                kwargs["n"] = 1
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                kwargs["n"] = 1
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                kwargs["n"] = 1
                return super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

        chat = _DeepSeekChat(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
            temperature=0,
        )
        # DeepSeek has no embeddings API — fall back to Ollama local
        try:
            from langchain_ollama import OllamaEmbeddings
        except ImportError:
            from langchain_community.embeddings import OllamaEmbeddings  # type: ignore
        emb = OllamaEmbeddings(model=ollama_embed_model)
    else:  # ollama
        try:
            from langchain_ollama import ChatOllama, OllamaEmbeddings
            chat = ChatOllama(model=ollama_model, temperature=0)
            emb = OllamaEmbeddings(model=ollama_embed_model)
        except ImportError:
            from langchain_community.chat_models import ChatOllama
            from langchain_community.embeddings import OllamaEmbeddings
            chat = ChatOllama(model=ollama_model, temperature=0)
            emb = OllamaEmbeddings(model=ollama_embed_model)
    return chat, emb


def compute_ragas(
    samples: list[dict],
    ragas_provider: str,
    ollama_model: str,
    ollama_embed_model: str,
) -> dict[str, float]:
    from datasets import Dataset
    from ragas import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.run_config import RunConfig

    chat, emb = _make_ragas_llm(ragas_provider, ollama_model, ollama_embed_model)
    llm = LangchainLLMWrapper(chat)
    embeddings = LangchainEmbeddingsWrapper(emb)

    try:
        from ragas.metrics.collections import (
            Faithfulness, ResponseRelevancy, ContextPrecision, ContextRecall,
        )
    except ImportError:
        from ragas.metrics import (  # type: ignore  # ragas < 0.4
            Faithfulness, ResponseRelevancy, ContextPrecision, ContextRecall,
        )
    metrics = [
        Faithfulness(llm=llm),
        ResponseRelevancy(llm=llm, embeddings=embeddings),
        ContextPrecision(llm=llm),
        ContextRecall(llm=llm),
    ]

    # RAGAS needs non-empty contexts; baseline uses a placeholder
    dataset = Dataset.from_dict({
        "question": [s["question"] for s in samples],
        "answer": [s["answer"] for s in samples],
        "contexts": [s["contexts"] or ["[no context — baseline mode]"] for s in samples],
        "ground_truth": [s["ground_truth"] for s in samples],
    })

    timeout = 600 if ragas_provider in ("anthropic", "openai") else 3600
    workers = 4 if ragas_provider in ("anthropic", "openai") else 1
    run_config = RunConfig(timeout=timeout, max_workers=workers)
    result = evaluate(dataset, metrics=metrics, run_config=run_config)
    # EvaluationResult has no .items(); use to_pandas() and take column means
    df = result.to_pandas()
    metric_cols = [c for c in df.columns if c not in ("question", "answer", "contexts", "ground_truth")]
    return {c: float(df[c].mean()) for c in metric_cols if df[c].dtype.kind in "fi"}


# ── scoring & output ──────────────────────────────────────────────────────────

def print_markdown_table(scores: dict[str, float]) -> None:
    print("\n## Eval Results\n")
    print(f"| {'Metric':<40} | {'Score':>6} |")
    print(f"| {'-'*40} | {'-'*6} |")
    for key, val in scores.items():
        label = METRIC_DISPLAY.get(key, key)
        if isinstance(val, float):
            print(f"| {label:<40} | {val:>6.3f} |")
        else:
            print(f"| {label:<40} | {str(val):>6} |")


def regression_check(current: dict, baseline: dict, threshold: float) -> list[str]:
    failures = []
    for metric, curr in current.items():
        if not isinstance(curr, float):
            continue
        base = baseline.get(metric)
        if base is None:
            continue
        drop = float(base) - curr
        if drop > threshold:
            failures.append(
                f"  {metric}: {base:.3f} → {curr:.3f}  (drop={drop:.3f} > {threshold})"
            )
    return failures


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="RAG eval runner")
    parser.add_argument("--questions", default="evals/golden_hpg.yaml")
    parser.add_argument("--out", default="evals/results.json")
    parser.add_argument("--baseline", default="evals/baseline.json")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save current scores as new baseline")
    parser.add_argument("--ollama-model",
                        default=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
                        help="Local Ollama model used as RAGAS judge (ollama provider only)")
    parser.add_argument("--ollama-embed-model",
                        default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                        help="Local Ollama model used for RAGAS embeddings (ollama provider only)")
    parser.add_argument("--ragas-provider",
                        default=os.environ.get("RAGAS_PROVIDER", "ollama"),
                        choices=["ollama", "anthropic", "openai", "deepseek"],
                        help="Provider for RAGAS judge (default: ollama)")
    parser.add_argument("--skip-ragas", action="store_true",
                        help="Run model calls only, skip RAGAS scoring")
    parser.add_argument("--only-refusal", action="store_true",
                        help="Run only no_answer/out_of_scope questions (fast CI check)")
    parser.add_argument("--collection",
                        help="Qdrant collection to use for RAG retrieval (enables RAG mode)")
    parser.add_argument("--embed",
                        default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                        help="Ollama embed model for RAG retrieval (used with --collection)")
    parser.add_argument("--retriever",
                        choices=["vector", "bm25", "hybrid_weighted", "hybrid_rrf", "hybrid_rerank", "rag_fusion", "router_sql"],
                        default="vector",
                        help="Retrieval method: vector (default), bm25, hybrid_weighted, hybrid_rrf, hybrid_rerank, rag_fusion, router_sql")
    parser.add_argument("--vn-tokenize",
                        action="store_true",
                        help="BM25 only: use underthesea Vietnamese word tokenization")
    args = parser.parse_args()

    questions = load_questions(Path(args.questions))
    client = create_client()

    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    rag_mode = bool(args.collection)
    bm25_retriever = None
    if rag_mode and args.retriever in ("bm25", "hybrid_weighted", "hybrid_rrf", "hybrid_rerank", "rag_fusion", "router_sql"):
        from rag.retrieval_bm25 import BM25Retriever
        bm25_retriever = BM25Retriever(
            collection=args.collection,
            use_vn_tokenize=args.vn_tokenize,
        )

    mode_label = "baseline (no context)"
    if rag_mode:
        if args.retriever == "bm25":
            tok = "vn_tokenize" if args.vn_tokenize else "raw_split"
            mode_label = f"BM25 ({tok}) — {args.collection}"
        elif args.retriever in ("hybrid_weighted", "hybrid_rrf", "hybrid_rerank"):
            tok = "vn_tokenize" if args.vn_tokenize else "raw_split"
            mode_label = f"{args.retriever} (BM25 {tok} + vector) — {args.collection}"
        elif args.retriever == "rag_fusion":
            tok = "vn_tokenize" if args.vn_tokenize else "raw_split"
            mode_label = f"rag_fusion (multi-query N=4, BM25 {tok} + vector) — {args.collection}"
        elif args.retriever == "router_sql":
            tok = "vn_tokenize" if args.vn_tokenize else "raw_split"
            mode_label = f"router_sql (classify → SQL | hybrid_rerank) — {args.collection}"
        else:
            mode_label = f"vector — {args.collection}"
    print(f"Provider      : {provider}")
    print(f"Mode          : {mode_label}")
    print(f"RAGAS provider: {args.ragas_provider}")
    print(f"Questions     : {len(questions)} total ({sum(q.get('indexed', True) for q in questions if q['group'] not in ('no_answer','out_of_scope'))} indexed, {len([q for q in questions if q['group'] in ('no_answer','out_of_scope')])} refusal)")
    if args.ragas_provider == "ollama":
        print(f"RAGAS judge   : {args.ollama_model}  embed: {args.ollama_embed_model}")
    print()

    all_eval_qs = [q for q in questions if q["group"] not in ("no_answer", "out_of_scope", "news")]
    skipped = [q for q in all_eval_qs if not q.get("indexed", True)]
    eval_qs = [] if args.only_refusal else [q for q in all_eval_qs if q.get("indexed", True)]
    refusal_qs = [q for q in questions if q["group"] in ("no_answer", "out_of_scope")]
    news_qs = [q for q in questions if q["group"] == "news"]
    if skipped:
        print(f"Skipped (indexed=false): {len(skipped)} câu {[q['id'] for q in skipped]}")

    # ── model calls ──
    samples: list[dict] = []
    for q in eval_qs:
        print(f"  {q['id']:6s} [{q['group']:<20}]", end="", flush=True)
        t0 = time.perf_counter()
        if rag_mode and args.retriever == "router_sql":
            answer, contexts = ask_with_router_sql(
                client, q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever == "rag_fusion":
            answer, contexts = ask_with_rag_fusion(
                q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever == "hybrid_rerank":
            answer, contexts = ask_with_hybrid_rerank(
                client, q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever in ("hybrid_weighted", "hybrid_rrf"):
            answer, contexts = ask_with_hybrid(
                client, q["question"], args.collection, args.embed,
                bm25_retriever, args.retriever,
            )
        elif rag_mode and bm25_retriever is not None:
            answer, contexts = ask_with_bm25(client, q["question"], bm25_retriever)
        elif rag_mode:
            answer, contexts = ask_with_rag(client, q["question"], args.collection, args.embed)
        else:
            answer, contexts = ask_baseline(client, q["question"])
        elapsed = time.perf_counter() - t0
        print(f" {elapsed:5.1f}s")
        samples.append({
            "id": q["id"],
            "group": q["group"],
            "question": q["question"],
            "answer": answer,
            "contexts": contexts,
            "ground_truth": q["answer"],
        })

    # ── refusal check ──
    refusal_results: list[dict] = []
    for q in refusal_qs:
        print(f"  {q['id']:6s} [{q['group']:<20}]", end="", flush=True)
        t0 = time.perf_counter()
        if rag_mode and args.retriever == "router_sql":
            answer, _ = ask_with_router_sql(
                client, q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever == "rag_fusion":
            answer, _ = ask_with_rag_fusion(
                q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever == "hybrid_rerank":
            answer, _ = ask_with_hybrid_rerank(
                client, q["question"], args.collection, args.embed, bm25_retriever,
            )
        elif rag_mode and args.retriever in ("hybrid_weighted", "hybrid_rrf"):
            answer, _ = ask_with_hybrid(
                client, q["question"], args.collection, args.embed,
                bm25_retriever, args.retriever,
            )
        elif rag_mode and bm25_retriever is not None:
            answer, _ = ask_with_bm25(client, q["question"], bm25_retriever)
        elif rag_mode:
            answer, _ = ask_with_rag(client, q["question"], args.collection, args.embed)
        else:
            answer, _ = ask_baseline(client, q["question"])
        elapsed = time.perf_counter() - t0
        passed = is_refusal(answer)
        print(f" {'PASS' if passed else 'FAIL'} {elapsed:5.1f}s")
        refusal_results.append({"id": q["id"], "group": q["group"],
                                 "passed": passed, "answer": answer})

    refusal_rate = (
        sum(r["passed"] for r in refusal_results) / len(refusal_results)
        if refusal_results else 1.0
    )

    # ── news pipeline check ──
    news_check_results: list[dict] = []
    if news_qs and rag_mode and args.retriever == "rag_fusion":
        from rag.rag_fusion_graph import run_rag_fusion
        print(f"\nNews pipeline check ({len(news_qs)} questions):")
        for q in news_qs:
            print(f"  {q['id']:6s} [{q['group']:<8}]", end="", flush=True)
            t0 = time.perf_counter()
            try:
                state = run_rag_fusion(
                    query=q["question"],
                    collection=args.collection,
                    embed_model=args.embed,
                    bm25_retriever=bm25_retriever,
                )
                sources = state.get("sources_used", [])
                passed = any("TIN TỨC" in s for s in sources)
            except Exception as e:
                sources = []
                passed = False
                print(f" [ERROR: {e}]", end="")
            elapsed = time.perf_counter() - t0
            print(f" {'PASS' if passed else 'FAIL'}  sources={sources}  {elapsed:.1f}s")
            news_check_results.append({
                "id": q["id"],
                "passed": passed,
                "sources_used": sources,
            })
    elif news_qs:
        print(f"\nNews check skipped — requires --retriever rag_fusion")

    news_pass_rate = (
        sum(r["passed"] for r in news_check_results) / len(news_check_results)
        if news_check_results else None
    )

    # ── RAGAS ──
    ragas_scores: dict = {}
    if not args.skip_ragas and samples:
        print("\nComputing RAGAS scores via Ollama (slow — ~30s/question)...")
        ragas_scores = compute_ragas(samples, args.ragas_provider, args.ollama_model, args.ollama_embed_model)

    all_scores = {**ragas_scores, "refusal_pass_rate": refusal_rate}
    if news_pass_rate is not None:
        all_scores["news_pipeline_pass_rate"] = news_pass_rate
    print_markdown_table(all_scores)
    print(f"\nRefusal: {sum(r['passed'] for r in refusal_results)}/{len(refusal_results)} passed")
    if news_check_results:
        n_pass = sum(r["passed"] for r in news_check_results)
        print(f"News pipeline: {n_pass}/{len(news_check_results)} passed")

    # ── save results ──
    output = {
        "scores": all_scores,
        "samples": samples,
        "refusal_results": refusal_results,
        "provider": provider,
        "retriever": args.retriever,
        "collection": args.collection,
        "vn_tokenize": getattr(args, "vn_tokenize", False),
        "ollama_judge": args.ollama_model,
    }
    Path(args.out).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Results  → {args.out}")

    # ── baseline ──
    baseline_path = Path(args.baseline)
    if args.save_baseline:
        baseline_path.write_text(
            json.dumps(all_scores, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Baseline → {args.baseline}")
        return

    if not baseline_path.exists():
        print(f"\nNo baseline at {args.baseline}. Run with --save-baseline first.")
        return

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    failures = regression_check(all_scores, baseline, THRESHOLD_DROP)
    if failures:
        print("\n❌  REGRESSION DETECTED:")
        for f in failures:
            print(f)
        sys.exit(1)
    else:
        print("\n✅  No regression (all metrics within threshold)")


if __name__ == "__main__":
    main()
