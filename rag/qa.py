"""
rag/qa.py — Thin Q&A dispatcher for conversation routing (Bài 31+).

answer(question, ticker=None, client=None) -> str

Routes to:
  SQL path  — if rag/router classifies as số_liệu or cả_hai
  RAG path  — if rag/router classifies as diễn_giải or cả_hai
  Decline   — if ngoài_phạm_vi

Falls back gracefully when Qdrant/Ollama unavailable.
"""

from __future__ import annotations

import os
from typing import Optional

from llm.types import Message
from rag.router import classify as route_classify

_COLLECTION = os.environ.get("RAG_COLLECTION", "hpg_b7_structural_meta")
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def _sql_answer(question: str, client) -> str:
    """Generate + execute SQL, return formatted answer."""
    from rag.sql_agent import execute_safe, SQLAgentError, SecurityError
    try:
        result = execute_safe(question, client=client)
        rows_text = result.format_answer()
        # Ask LLM to narrate the result
        resp = client.generate(
            messages=[Message(
                role="user",
                content=(
                    f"Câu hỏi: {question}\n\n"
                    f"Dữ liệu từ DB:\n{rows_text}\n\n"
                    "Trả lời ngắn gọn bằng tiếng Việt, trích dẫn số liệu cụ thể."
                ),
            )],
            system="Bạn là trợ lý phân tích tài chính. Dựa vào dữ liệu đã cho, trả lời câu hỏi.",
            max_tokens=512,
        )
        return resp.text.strip()
    except SecurityError as exc:
        return f"Câu hỏi không thể thực thi vì lý do bảo mật: {exc}"
    except Exception as exc:
        return f"Lỗi truy vấn DB: {exc}"


def _rag_answer(question: str, ticker: Optional[str], client) -> str:
    """Vector search + LLM synthesis from HPG financial documents."""
    try:
        import httpx
        from qdrant_client import QdrantClient

        # Embed question
        r = httpx.post(
            f"{_OLLAMA_URL}/api/embeddings",
            json={"model": _EMBED_MODEL, "prompt": question},
            timeout=30,
        )
        r.raise_for_status()
        qvec = r.json()["embedding"]

        qdrant = QdrantClient("localhost", port=6333)
        points = qdrant.query_points(
            collection_name=_COLLECTION,
            query=qvec,
            limit=5,
        ).points

        if not points:
            return "Không tìm thấy thông tin liên quan trong tài liệu HPG."

        chunks = "\n\n---\n\n".join(
            p.payload.get("text", "") for p in points
        )

        resp = client.generate(
            messages=[Message(
                role="user",
                content=(
                    f"Câu hỏi: {question}\n\n"
                    f"Ngữ cảnh từ tài liệu HPG:\n{chunks}\n\n"
                    "Trả lời dựa trên ngữ cảnh trên. Nếu không có đủ thông tin, nói rõ."
                ),
            )],
            system=(
                "Bạn là trợ lý phân tích tài chính HPG. "
                "Trả lời bằng tiếng Việt, dựa chỉ vào ngữ cảnh được cung cấp. "
                "Trích dẫn nguồn khi có thể."
            ),
            max_tokens=1024,
        )
        return resp.text.strip()

    except Exception as exc:
        # Qdrant/Ollama unavailable — LLM-only fallback
        resp = client.generate(
            messages=[Message(role="user", content=question)],
            system=(
                "Bạn là trợ lý phân tích tài chính HPG. "
                "Lưu ý: hệ thống tìm kiếm tài liệu hiện không khả dụng. "
                "Trả lời từ kiến thức chung, thêm ghi chú '⚠️ Không có dữ liệu tài liệu'."
            ),
            max_tokens=512,
        )
        return resp.text.strip()


def answer(question: str, ticker: Optional[str] = None, client=None) -> str:
    """Route question and return answer string."""
    if client is None:
        from llm.factory import create_client
        client = create_client()

    route = route_classify(question)

    if route.label == "ngoài_phạm_vi":
        return (
            f"Câu hỏi này nằm ngoài phạm vi dữ liệu hiện có.\n"
            f"Lý do: {route.reason}"
        )

    if route.label == "số_liệu":
        return _sql_answer(question, client)

    if route.label == "diễn_giải":
        return _rag_answer(question, ticker, client)

    if route.label == "cả_hai":
        sql_part = _sql_answer(question, client)
        rag_part = _rag_answer(question, ticker, client)
        return f"**Dữ liệu số:**\n{sql_part}\n\n**Ngữ cảnh tài liệu:**\n{rag_part}"

    # Fallback
    return _rag_answer(question, ticker, client)
