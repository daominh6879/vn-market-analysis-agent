"""
rag/multi_query.py — Structured query decomposition + source tagging for RAG-Fusion.

decompose_query() uses tool calling to return structured SubTask objects directly
(intent + tickers + question). No free-text re-classification needed downstream.
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

_VALID_INTENTS = [
    "price_action", "technical_analysis", "news_sentiment",
    "macro_sector", "investment_case", "screening",
    "rag_qa", "breakout_scan",
]

_DECOMPOSE_TOOL = {
    "name": "decompose_query",
    "description": (
        "Phân tách câu hỏi phân tích tài chính thành các sub-task độc lập. "
        "Mỗi sub-task có intent rõ ràng, danh sách mã cổ phiếu cụ thể, và câu hỏi standalone."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": _VALID_INTENTS,
                            "description": "Loại phân tích cần thực hiện",
                        },
                        "tickers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Mã cổ phiếu VN cần tra cứu (ví dụ: VCB, CTG, BID). Bắt buộc có ít nhất 1 mã.",
                        },
                        "question": {
                            "type": "string",
                            "description": "Câu hỏi standalone đầy đủ ngữ cảnh cho sub-task này.",
                        },
                    },
                    "required": ["intent", "tickers", "question"],
                },
            }
        },
        "required": ["tasks"],
    },
}

_INTENT_DESCRIPTIONS = {
    "price_action":       "biến động giá, khối lượng, OHLCV, phiên giao dịch",
    "technical_analysis": "chỉ báo kỹ thuật RSI, MACD, MA, Bollinger, breakout",
    "news_sentiment":     "tin tức, sự kiện, khối ngoại, tâm lý thị trường",
    "macro_sector":       "vĩ mô, ngành, so sánh chỉ số, tương quan thị trường",
    "investment_case":    "định giá, PE, ROE, cơ bản doanh nghiệp, luận điểm đầu tư",
    "screening":          "sàng lọc cổ phiếu theo tiêu chí, top tăng/giảm",
    "rag_qa":             "báo cáo tài chính BCTC, số liệu kế toán, kiểm toán",
    "breakout_scan":      "breakout, phá vỡ kháng cự/hỗ trợ, mô hình giá",
}


def decompose_query(query: str, n: int = 4, ticker: str = "") -> list[dict]:
    """Decompose a financial query into structured sub-tasks via tool calling.

    Returns list of dicts: [{intent, tickers, question}, ...].
    Falls back to single task wrapping original query on tool call failure.

    Args:
        ticker: classifier-extracted ticker/sector (injected as constraint).
    """
    client = create_client()

    intent_guide = "\n".join(f"- {k}: {v}" for k, v in _INTENT_DESCRIPTIONS.items())
    ticker_hint = (
        f"\nChủ thể câu hỏi: '{ticker}'. Dùng đúng mã/ngành này, không thêm mã không liên quan."
        if ticker else ""
    )

    prompt = (
        f"Phân tách câu hỏi sau thành {n} sub-task phân tích tài chính đa góc nhìn.\n"
        f"Câu hỏi: {query}{ticker_hint}\n\n"
        f"Hướng dẫn chọn intent:\n{intent_guide}\n\n"
        "Yêu cầu:\n"
        "- Mỗi sub-task một intent khác nhau nếu có thể\n"
        "- tickers: mã cổ phiếu cụ thể (2-4 ký tự viết hoa, VD: VCB, CTG, BID)\n"
        "- question: câu hỏi đầy đủ, standalone, chứa ticker và thời gian nếu cần"
    )

    resp = client.generate(
        [Message(role="user", content=prompt)],
        max_tokens=1024,
        system="Bạn là chuyên gia phân tích tài chính Việt Nam. Gọi tool decompose_query với kết quả phân tách.",
        tools=[_DECOMPOSE_TOOL],
    )

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        tasks = tc.input.get("tasks", [])
        # Validate: drop tasks with no tickers or invalid intent
        valid = [
            t for t in tasks
            if t.get("intent") in _VALID_INTENTS
            and t.get("tickers")
            and t.get("question")
        ]
        if valid:
            return valid[:n]

    # Fallback: single macro_sector task
    return [{"intent": "macro_sector", "tickers": [ticker] if ticker else [], "question": query}]


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
