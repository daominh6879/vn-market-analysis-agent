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

from core.config import settings
from llm.types import Message
from rag.router import classify as route_classify

_COLLECTION = os.environ.get("RAG_COLLECTION", "bctc_structural")
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Lazy BM25 cache — same pattern as agents/graph.py
_bm25_cache: dict[str, object] = {}


def _get_bm25(collection: str):
    if collection not in _bm25_cache:
        from rag.retrieval_bm25 import BM25Retriever
        _bm25_cache[collection] = BM25Retriever(collection=collection, use_vn_tokenize=True)
    return _bm25_cache[collection]


def _sql_answer_from_rows(question: str, rows_text: str, client) -> str:
    """Synthesize the final answer from SQL rows already fetched by retrieve_only().

    Does NOT call execute_safe() itself — text-to-SQL is nondeterministic, so a second
    independent call here could silently generate a different query than the one whose
    rows are shown as "context", making the cited context diverge from the answer.
    """
    resp = client.generate(
        messages=[Message(
            role="user",
            content=(
                f"Câu hỏi: {question}\n\n"
                f"Dữ liệu từ DB:\n{rows_text}\n\n"
                "Trả lời ngắn gọn bằng tiếng Việt, trích dẫn số liệu cụ thể. "
                "Đổi đơn vị cho dễ đọc (chia 1e9 → tỷ đồng)."
            ),
        )],
        system=(
            "Bạn là trợ lý phân tích tài chính. Dựa vào dữ liệu đã cho, trả lời câu hỏi. "
            "KHÔNG giải thích cách làm, KHÔNG suy luận, KHÔNG nhắc tên cột hay metric_code. "
            "Trả lời TRỰC TIẾP bằng 1-3 câu sạch."
        ),
        max_tokens=512,
    )
    return resp.text.strip()


def _rag_answer(question: str, ticker: Optional[str], client) -> str:
    """RAG-Fusion retrieval + LLM synthesis from financial documents.

    Uses multi-query decomposition + RRF fusion (rag/rag_fusion_graph.py).
    Falls back to plain Qdrant single-query on infrastructure failure.
    """
    try:
        from rag.rag_fusion_graph import run_rag_fusion
        bm25 = _get_bm25(_COLLECTION)
        result = run_rag_fusion(
            query=question,
            collection=_COLLECTION,
            embed_model=_EMBED_MODEL,
            bm25_retriever=bm25,
            ticker=ticker,
            n_sub_queries=4,
        )
        return result.get("report") or result.get("analysis") or "Không tìm thấy thông tin liên quan."

    except Exception:
        # Fallback: plain single-query Qdrant search
        try:
            import httpx
            from qdrant_client import QdrantClient

            r = httpx.post(
                f"{_OLLAMA_URL}/api/embeddings",
                json={"model": _EMBED_MODEL, "prompt": question},
                timeout=30,
            )
            r.raise_for_status()
            qvec = r.json()["embedding"]

            qdrant = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)
            search_filter = None
            if ticker:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                search_filter = Filter(
                    must=[FieldCondition(key="ticker", match=MatchValue(value=ticker.upper()))]
                )
            points = qdrant.query_points(
                collection_name=_COLLECTION,
                query=qvec,
                query_filter=search_filter,
                limit=5,
            ).points

            if not points:
                return "Không tìm thấy thông tin liên quan trong tài liệu."

            chunks = "\n\n---\n\n".join(p.payload.get("text", "") for p in points)
            resp = client.generate(
                messages=[Message(
                    role="user",
                    content=f"Câu hỏi: {question}\n\nNgữ cảnh:\n{chunks}\n\nTrả lời dựa trên ngữ cảnh trên.",
                )],
                system=(
                    "Bạn là trợ lý phân tích tài chính. "
                    "Trả lời bằng tiếng Việt, dựa chỉ vào ngữ cảnh được cung cấp."
                ),
                max_tokens=1024,
            )
            return resp.text.strip()

        except Exception:
            resp = client.generate(
                messages=[Message(role="user", content=question)],
                system=(
                    "Bạn là trợ lý phân tích tài chính. "
                    "Lưu ý: hệ thống tìm kiếm tài liệu hiện không khả dụng. "
                    "Trả lời từ kiến thức chung, thêm ghi chú '⚠️ Không có dữ liệu tài liệu'."
                ),
                max_tokens=512,
            )
            return resp.text.strip()


def _fetch_sql_rows_text(question: str, client) -> str:
    """Run text-to-SQL once and format the rows. Single source of truth for the SQL
    path — retrieve_only() and answer() both call this so the context shown to the
    caller is always exactly what the SQL answer is synthesized from (text-to-SQL is
    nondeterministic, so calling execute_safe() a second time for the same question
    can silently return different rows and make the cited context diverge from the
    generated answer)."""
    try:
        from rag.sql_agent import execute_safe
        result = execute_safe(question, client=client)
        if result.rows:
            return f"[DỮ LIỆU SQL]\n{result.format_answer()}"
        return ""
    except Exception as exc:
        return f"[DỮ LIỆU SQL]\nLỗi: {exc}"


def retrieve_only(question: str, ticker: Optional[str] = None, client=None) -> str:
    """Retrieve context (SQL rows + RAG chunks) without LLM synthesis."""
    route = route_classify(question)
    if route.label == "ngoài_phạm_vi":
        return "Ngoài phạm vi: dữ liệu hiện có chưa bao gồm nội dung này."

    if client is None:
        from llm.factory import create_client as _create
        client = _create()

    parts: list[str] = []

    if route.label in ("số_liệu", "cả_hai"):
        sql_part = _fetch_sql_rows_text(question, client)
        if sql_part:
            parts.append(sql_part)

    if route.label in ("diễn_giải", "cả_hai"):
        try:
            from rag.rag_fusion_graph import make_multi_retrieve_node, make_rrf_fuse_node
            from rag.multi_query import generate_sub_queries
            bm25 = _get_bm25(_COLLECTION)
            sub_queries = generate_sub_queries(question, n=4)
            state: dict = {"query": question, "ticker": ticker, "sub_queries": sub_queries}
            retrieve_fn = make_multi_retrieve_node(_COLLECTION, _EMBED_MODEL, bm25)
            state = {**state, **retrieve_fn(state)}
            fuse_fn = make_rrf_fuse_node(top_k=5)
            state = {**state, **fuse_fn(state)}
            chunks = state.get("fused_chunks", [])
            if chunks:
                parts.append("[TÀI LIỆU RAG]\n" + "\n\n---\n\n".join(chunks[:5]))
        except Exception as exc:
            parts.append(f"[TÀI LIỆU RAG]\nLỗi: {exc}")

    return "\n\n".join(parts) if parts else "Không tìm thấy dữ liệu liên quan."


def answer(question: str, ticker: Optional[str] = None, client=None) -> str:
    """Route question and return answer string."""
    if client is None:
        from llm.factory import create_client
        client = create_client()

    route = route_classify(question)

    if route.label == "ngoài_phạm_vi":
        return (
            "Câu hỏi này nằm ngoài phạm vi dữ liệu hiện có. "
            "Bạn có thể hỏi về giá, chỉ số tài chính, báo cáo tài chính, cổ tức hoặc định giá của một mã cụ thể."
        )

    if route.label == "số_liệu":
        rows_text = _fetch_sql_rows_text(question, client)
        if not rows_text:
            return "Không tìm thấy dữ liệu liên quan trong cơ sở dữ liệu."
        return _sql_answer_from_rows(question, rows_text, client)

    if route.label == "diễn_giải":
        return _rag_answer(question, ticker, client)

    if route.label == "cả_hai":
        rows_text = _fetch_sql_rows_text(question, client)
        sql_part = (
            _sql_answer_from_rows(question, rows_text, client)
            if rows_text else "Không tìm thấy dữ liệu liên quan trong cơ sở dữ liệu."
        )
        rag_part = _rag_answer(question, ticker, client)
        return f"**Dữ liệu số:**\n{sql_part}\n\n**Ngữ cảnh tài liệu:**\n{rag_part}"

    # Fallback
    return _rag_answer(question, ticker, client)
