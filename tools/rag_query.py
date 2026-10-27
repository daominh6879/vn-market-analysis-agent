"""
tools/rag_query.py — RAG document Q&A with filter-based routing.

Single collection bctc_structural holds all tickers.
Tickers, sector, year narrow results via Qdrant payload filters.

Examples:
  ask_report("Tổng tài sản HPG 2025?", tickers=["HPG"])
  ask_report("So sánh HPG và VCB", tickers=["HPG", "VCB"])
  ask_report("Ngành thép lãi như thế nào?", sector="steel")
  ask_report("Ai có nợ cao nhất?")  # no filter — search all
"""
from __future__ import annotations

import os
import threading

import httpx
from qdrant_client import QdrantClient

from core.config import settings
from rag.filter import BCTC_COLLECTION, build_filter
from rag.lexical import fold, hybrid_rank, is_primary_statement, lexical_score, query_ngrams
from tools.result import ToolResult

OLLAMA_URL  = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
TOP_K       = 5       # vector-only retrieval
HYBRID_TOP_K = 6      # hybrid returns one more: lexical hits are added without displacing the vector anchors
VECTOR_ANCHORS = 3    # vector hits that always stay in the hybrid result
VECTOR_POOL = 40      # vector candidates fused with the lexical ranking
LEXICAL_WEIGHT = 2.0  # the best text match is worth this many top vector ranks (vector ranks of OCR'd tables are noisy)
SCAN_LIMIT  = 1500    # max filtered chunks scanned lexically (a few hundred per ticker)

_SYSTEM = (
    "Bạn là trợ lý tài chính. Trả lời ngắn gọn, đi thẳng vào trọng tâm — không dài dòng. "
    "Dựa vào các đoạn tài liệu dưới đây. "
    "Nếu thông tin không có trong tài liệu, nói rõ 'Không có trong tài liệu'. "
    "Mỗi đoạn có thể bắt đầu bằng nhãn [Ngữ cảnh: tên báo cáo | ngày | Cột: ...] cho biết nó thuộc báo cáo nào và cột nào là số cuối năm. "
    "Chỉ khi câu hỏi hỏi một con số tổng hợp (tổng tài sản, nợ phải trả, vốn chủ sở hữu, doanh thu, lợi nhuận...): ưu tiên số trên báo cáo chính "
    "(bảng cân đối kế toán / báo cáo tình hình tài chính, kết quả hoạt động, lưu chuyển tiền tệ) hơn số trong thuyết minh; "
    "nếu hai nguồn khác nhau, nêu số báo cáo chính trước và ghi chú số còn lại. "
    "Nếu câu hỏi về chính sách, nguyên tắc hay cách ghi nhận / trích lập (hỏi \"như thế nào\"), trả lời bằng nội dung chính sách trong thuyết minh, không trả bằng số dư."
)

_qdrant: QdrantClient | None = None
_qdrant_lock = threading.Lock()


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        with _qdrant_lock:
            if _qdrant is None:
                _qdrant = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    return _qdrant


def _embed(text: str) -> list[float]:
    # /api/embed (Ollama >= 0.1.31) uses "input"; /api/embeddings (legacy) uses "prompt"
    endpoints = [
        ("/api/embed",       {"model": EMBED_MODEL, "input": text}),
        ("/api/embeddings",  {"model": EMBED_MODEL, "prompt": text}),
    ]
    for path, payload in endpoints:
        try:
            r = httpx.post(f"{OLLAMA_URL}{path}", json=payload, timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            # /api/embed → {"embeddings": [[float,...]]}
            # /api/embeddings → {"embedding": [float,...]}
            vec = data.get("embedding") or (data.get("embeddings") or [[]])[0]
            if vec:
                return vec
        except httpx.HTTPStatusError:
            continue
    raise httpx.HTTPError("Ollama embed failed on both /api/embed and /api/embeddings")


def _retrieve(
    question: str,
    tickers: list[str] | None = None,
    sector: str | None = None,
    year: str | None = None,
    top_k: int | None = None,
    hybrid: bool = True,
) -> list[str]:
    """Vector search, fused (RRF) with an accent/space-insensitive lexical ranking.

    Scanned BCTC tables lose spaces and diacritics in OCR, which sinks the right chunk in pure vector search.
    With a payload filter every matching chunk is scored lexically; without one, only the vector candidates are.
    """
    top_k = top_k or (HYBRID_TOP_K if hybrid else TOP_K)
    vec = _embed(question)
    q_filter = build_filter(tickers=tickers, sector=sector, year=year)
    qdrant = _get_qdrant()
    pts = qdrant.query_points(
        collection_name=BCTC_COLLECTION,
        query=vec,
        query_filter=q_filter,
        limit=max(top_k, VECTOR_POOL) if hybrid else top_k,
    ).points
    if not hybrid:
        return [p.payload.get("text", "") for p in pts if p.payload and p.payload.get("text")]

    grams = query_ngrams(question, drop={fold(t) for t in tickers or []})
    pool = {p.id: p.payload.get("text", "") for p in pts if p.payload}
    if q_filter is not None and grams:
        scanned, offset = 0, None
        while scanned < SCAN_LIMIT:
            batch, offset = qdrant.scroll(
                collection_name=BCTC_COLLECTION, scroll_filter=q_filter, limit=256, offset=offset, with_payload=True,
            )
            for p in batch:
                if p.payload:
                    pool.setdefault(p.id, p.payload.get("text", ""))
            scanned += len(batch)
            if offset is None:
                break
    scores = {pid: lexical_score(fold(text), grams) for pid, text in pool.items()} if grams else {}
    primary = {pid for pid, sc in scores.items() if sc > 0 and is_primary_statement(pool[pid])}
    fused = hybrid_rank([p.id for p in pts], scores, boost_ids=primary, lexical_weight=LEXICAL_WEIGHT,
                        anchors=VECTOR_ANCHORS)
    return [pool[pid] for pid in fused[:top_k] if pool.get(pid)]


def _llm_answer(question: str, contexts: list[str]) -> str:
    from llm.factory import create_client
    from llm.types import Message

    ctx_block = "\n\n---\n\n".join(contexts) if contexts else "[không có ngữ cảnh]"
    system = f"{_SYSTEM}\n\nTÀI LIỆU:\n{ctx_block}"
    client = create_client()
    resp = client.generate(
        [Message(role="user", content=question)],
        max_tokens=512,
        system=system,
    )
    return resp.text.strip()


def ask_report(
    question: str,
    tickers: list[str] | None = None,
    sector: str | None = None,
    year: str | None = None,
) -> ToolResult:
    """Query BCTC collection with optional filters.

    question: câu hỏi về nội dung báo cáo tài chính
    tickers:  list mã CK e.g. ["HPG"] or ["HPG", "VCB"] — None = tất cả
    sector:   ngành e.g. "steel", "banking" — None = không lọc
    year:     năm tài chính e.g. "2025" — None = tất cả năm
    """
    if not question.strip():
        return ToolResult(status="invalid_input", data=None, message="question rỗng.")

    if tickers is not None:
        invalid = [t for t in tickers if not t.isalpha()]
        if invalid:
            return ToolResult(
                status="invalid_input",
                data=None,
                message=f"ticker không hợp lệ: {invalid}. Dùng mã CK như HPG, VCB.",
            )

    try:
        existing = {c.name for c in _get_qdrant().get_collections().collections}
        if BCTC_COLLECTION not in existing:
            return ToolResult(
                status="no_data",
                data=None,
                message=(
                    f"Collection '{BCTC_COLLECTION}' chưa tồn tại. "
                    "Cần index BCTC qua Dagster pipeline trước."
                ),
            )

        contexts = _retrieve(question, tickers=tickers, sector=sector, year=year)
        if not contexts:
            filter_desc = ""
            if tickers:
                filter_desc = f" cho {tickers}"
            elif sector:
                filter_desc = f" ngành {sector}"
            return ToolResult(
                status="no_data",
                data=None,
                message=f"Không tìm thấy đoạn tài liệu liên quan{filter_desc} trong {BCTC_COLLECTION}.",
            )

        answer = _llm_answer(question, contexts)
        filter_info = f"tickers={tickers}" if tickers else (f"sector={sector}" if sector else "no filter")
        return ToolResult(
            status="ok",
            data=answer,
            message=f"Trả lời từ {BCTC_COLLECTION} ({filter_info}, {len(contexts)} chunks).",
        )

    except httpx.HTTPError as exc:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Ollama embed lỗi: {exc}. Kiểm tra Ollama đang chạy.",
        )
    except Exception as exc:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"RAG query lỗi: {exc}",
        )
