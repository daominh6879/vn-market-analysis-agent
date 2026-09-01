"""
rag/multi_query.py — Sub-query generation and source tagging for RAG-Fusion.

RAG-Fusion idea: one query has multiple "angles". Decompose into N sub-queries,
retrieve for each independently, then fuse with RRF.

Trap guard: sub-queries can drift far from the original (especially short queries).
Constraint injected in prompt: all sub-queries must ask about the same company
and same time period as the original.
"""
from __future__ import annotations

import json
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

from llm.factory import create_client
from llm.types import Message


def generate_sub_queries(query: str, n: int = 4) -> list[str]:
    """Use LLM to decompose query into N sub-queries, each covering a different angle.

    Returns at most n sub-queries (falls back to [query] on parse/empty error).
    """
    import re as _re

    client = create_client()
    prompt = (
        f"Sinh {n} truy vấn con đa dạng (góc nhìn khác nhau) cho câu hỏi "
        f"phân tích sau, mỗi dòng một truy vấn, không đánh số:\n{query}"
    )

    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=512,
        system=(
            "Bạn là chuyên gia phân tích tài chính. "
            "Trả về đúng số truy vấn yêu cầu, mỗi dòng một truy vấn, không đánh số, không giải thích. "
            "QUAN TRỌNG: Chỉ đề cập đúng các mã cổ phiếu/ngành/chỉ số có trong câu hỏi gốc. "
        ),
    )
    raw = resp.text.strip()
    # Strip <think>...</think> blocks (deepseek reasoning mode)
    raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # Drop leading numbering if model ignores the instruction
    cleaned = []
    for ln in lines:
        ln = _re.sub(r"^\s*\d+[\.\)]\s*", "", ln)
        if ln:
            cleaned.append(ln)
    result = cleaned[:n]
    return result if result else [query]


def tag_source(chunk: str, metadata: dict) -> str:
    """Prefix a chunk with its source label so the LLM knows where data came from."""
    src = metadata.get("source_type", "unknown")
    if src == "news":
        return f"[TIN TỨC {metadata.get('date', '')}] {chunk}"
    elif src == "financial_report":
        return f"[BCTC {metadata.get('period', '')}] {chunk}"
    elif src == "historical_price":
        return f"[GIÁ LỊCH SỬ] {chunk}"
    return chunk


def query_postgres_facts(ticker: str, period: str | None = None, limit: int = 20) -> list[dict]:
    """Query financial_facts from Postgres for a ticker (and optional period).

    Returns list of dicts with keys: metric_code, period, value, unit.
    Returns [] if DB is unavailable.
    """
    try:
        from data.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                if period:
                    cur.execute(
                        """
                        SELECT metric_code, period, value, unit
                        FROM financial_facts
                        WHERE ticker = %s AND period = %s
                        ORDER BY metric_code
                        LIMIT %s
                        """,
                        (ticker, period, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT metric_code, period, value, unit
                        FROM financial_facts
                        WHERE ticker = %s
                        ORDER BY period DESC, metric_code
                        LIMIT %s
                        """,
                        (ticker, limit),
                    )
                rows = cur.fetchall()
                return [
                    {"metric_code": r[0], "period": r[1], "value": r[2], "unit": r[3]}
                    for r in rows
                ]
    except Exception:
        return []


def format_postgres_facts_as_text(facts: list[dict], ticker: str) -> str:
    """Convert Postgres fact rows to tagged text chunk for LLM context."""
    if not facts:
        return ""
    lines = [f"[GIÁ LỊCH SỬ] Dữ liệu tài chính {ticker} từ Postgres:"]
    for f in facts:
        val_fmt = f"{f['value']:,.0f}" if isinstance(f["value"], (int, float)) else str(f["value"])
        lines.append(f"  {f['period']} | {f['metric_code']}: {val_fmt} {f.get('unit', '')}")
    return "\n".join(lines)
