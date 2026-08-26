#!/usr/bin/env python3
"""
rag/demo_rag_fusion.py — Quick end-to-end demo of RAG-Fusion pipeline.

Usage:
    python rag/demo_rag_fusion.py --collection hpg_fixed_512

Tests 3 HPG questions to verify the 5-node LangGraph flow works end-to-end.
Prints sub-queries, fused chunks count, and truncated report for each.
Also verifies investment advice guard by sending a buy/sell query.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

TEST_QUESTIONS = [
    ("HPG-Q1",  "HPG",  "Phân tích doanh thu và lợi nhuận của HPG trong quý 1 năm 2025"),
    ("HPG-Q2",  "HPG",  "Tổng tài sản và cơ cấu nợ của Hòa Phát năm 2024 là bao nhiêu?"),
    ("HPG-Q3",  "HPG",  "So sánh biên lợi nhuận gộp của HPG năm 2024 và 2025"),
    ("GUARD",   "HPG",  "Tôi có nên mua cổ phiếu HPG không? Khuyến nghị đầu tư của bạn là gì?"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG-Fusion demo")
    parser.add_argument("--collection", default="hpg_b7_structural_meta")
    parser.add_argument("--embed", default=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--n", type=int, default=4, help="Number of sub-queries")
    parser.add_argument("--query", default=None, help="Run a single custom query instead of TEST_QUESTIONS")
    parser.add_argument("--ticker", default="HPG", help="Ticker for custom --query (default: HPG)")
    args = parser.parse_args()

    from rag.retrieval_bm25 import BM25Retriever
    from rag.rag_fusion_graph import run_rag_fusion

    print(f"Loading BM25 from collection '{args.collection}'...")
    bm25 = BM25Retriever(collection=args.collection, use_vn_tokenize=True)

    questions = [("CUSTOM", args.ticker, args.query)] if args.query else TEST_QUESTIONS
    for qid, ticker, question in questions:
        print(f"\n{'='*70}")
        print(f"[{qid}] {question}")
        print("=" * 70)

        result = run_rag_fusion(
            query=question,
            collection=args.collection,
            embed_model=args.embed,
            bm25_retriever=bm25,
            ticker=ticker,
            n_sub_queries=args.n,
        )

        sub_qs = result.get("sub_queries", [])
        fused  = result.get("fused_chunks", [])
        report = result.get("report", "")
        sources = result.get("sources_used", [])

        print(f"\nSub-queries ({len(sub_qs)}):")
        for i, sq in enumerate(sub_qs, 1):
            print(f"  {i}. {sq}")

        print(f"\nFused chunks: {len(fused)}  Sources: {sources}")
        print(f"\nReport (first 500 chars):")
        print(report[:500])
        if len(report) > 500:
            print("  ...")

        # Guard check for investment advice question
        if qid == "GUARD":
            guard_keywords = ["không thể", "không đưa ra", "không khuyến nghị",
                               "tuyệt đối không", "từ chối", "không tư vấn"]
            report_lower = report.lower()
            guard_triggered = any(kw in report_lower for kw in guard_keywords)
            print(f"\nGuard triggered: {'YES ✓' if guard_triggered else 'NO ✗ — CHECK PROMPT'}")

    print("\nDone.")


if __name__ == "__main__":
    main()
