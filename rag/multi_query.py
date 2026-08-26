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

    Angles: số liệu (metrics) · so sánh (comparison) · ngữ cảnh ngành (sector context)
            · sự kiện gần đây (recent events).

    Guard: all sub-queries must stay on the same company and time period.
    Returns at most n sub-queries (falls back to [query] on parse error).
    """
    client = create_client()
    prompt = f"""Nhiệm vụ: sinh đúng {n} câu hỏi con từ câu gốc bên dưới.

Quy tắc bắt buộc:
1. Phải có đúng {n} câu — không được ít hơn, dù câu gốc có đơn giản đến đâu.
2. Mỗi câu hỏi về CÙNG công ty và CÙNG kỳ thời gian với câu gốc.
3. Mỗi câu nhấn một góc khác nhau: (a) số liệu cụ thể, (b) so sánh kỳ trước, (c) ngữ cảnh ngành, (d) sự kiện ảnh hưởng.
4. Chỉ trả về JSON array of strings. Không có text nào khác.

Ví dụ — câu gốc: "Doanh thu HPG năm 2023?"
Output: ["Doanh thu thuần của HPG năm 2023 là bao nhiêu tỷ đồng?", "So với năm 2022, doanh thu HPG 2023 tăng hay giảm bao nhiêu phần trăm?", "Trong bối cảnh ngành thép Việt Nam, doanh thu HPG 2023 đứng ở vị trí nào?", "Yếu tố nào tác động lớn nhất đến doanh thu HPG năm 2023?"]

Câu gốc: {query}
Output (JSON array, đúng {n} phần tử):"""

    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=512,
        system="Bạn là chuyên gia phân tích tài chính. Trả về JSON array of strings. Không giải thích. Không có text nào khác ngoài JSON array.",
    )
    import re as _re
    raw = resp.text.strip()
    # Strip <think>...</think> blocks (deepseek reasoning mode)
    raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # Extract first complete JSON array — handles prose before AND after the array
    start = raw.find("[")
    if start >= 0:
        depth, end = 0, -1
        for i, ch in enumerate(raw[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            raw = raw[start:end]
    try:
        sub_queries = json.loads(raw)
        if not isinstance(sub_queries, list):
            raise ValueError("not a list")
        return [str(q) for q in sub_queries[:n]]
    except (json.JSONDecodeError, ValueError):
        return [query]


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
