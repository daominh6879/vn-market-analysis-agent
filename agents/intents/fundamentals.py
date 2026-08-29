"""
agents/intents/fundamentals.py — Nhóm 3: Cơ bản & Định giá.

Routes to rag/qa.py with single-company financial context.
Handles: P/E, P/B, ROE, D/E, margins, EPS, earnings growth.
"""

from __future__ import annotations


def run(ticker: str | None, query: str) -> str:
    """Answer via RAG/SQL path — single company financial analysis."""
    from rag.qa import answer as qa_answer
    return qa_answer(query, ticker=ticker)
