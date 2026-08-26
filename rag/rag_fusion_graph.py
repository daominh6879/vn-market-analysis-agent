"""
rag/rag_fusion_graph.py — RAG-Fusion pipeline via LangGraph.

Flow (5 nodes):
  decompose → multi_retrieve → rrf_fuse → analyze → report

decompose:
  LLM generates N sub-queries from the original query.

multi_retrieve:
  For each sub-query, run hybrid retrieval (BM25 + vector) in parallel
  via asyncio.gather. Also queries Postgres for structured financial facts
  and optionally Tavily for web news.

rrf_fuse:
  All per-sub-query result lists are merged with RRF into one ranked list.

analyze:
  LLM reads fused context (tagged by source) and synthesizes an analysis.
  GUARD: must NOT give buy/sell/hold advice on any stock.

report:
  Formats final answer with source citations and data table.

State keys:
  query           str           original user query
  sub_queries     list[str]     generated sub-queries
  raw_results     list[list[str]]  per-sub-query retrieved chunks
  fused_chunks    list[str]     RRF-merged chunks
  analysis        str           LLM analysis text
  report          str           final formatted report
  sources_used    list[str]     source labels seen
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import TypedDict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from langgraph.graph import StateGraph, END

from llm.factory import create_client
from llm.types import Message
from rag.multi_query import generate_sub_queries, query_postgres_facts, format_postgres_facts_as_text
from rag.fusion import rrf_fusion


# ── State ─────────────────────────────────────────────────────────────────────

class FusionState(TypedDict, total=False):
    query: str
    ticker: str
    sub_queries: list[str]
    raw_results: list[list[str]]
    fused_chunks: list[str]
    analysis: str
    report: str
    sources_used: list[str]


# ── Retrieval helpers ──────────────────────────────────────────────────────────

def _hybrid_retrieve(
    query: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
    top_k: int = 20,
) -> list[str]:
    """Single-query hybrid retrieval: BM25 + vector → RRF → top_k texts."""
    import httpx
    from qdrant_client import QdrantClient

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    r = httpx.post(
        f"{ollama_url}/api/embeddings",
        json={"model": embed_model, "prompt": query},
        timeout=30,
    )
    r.raise_for_status()
    qvec = r.json()["embedding"]

    qdrant = QdrantClient("localhost", port=6333)
    points = qdrant.query_points(collection_name=collection, query=qvec, limit=top_k).points
    vec_scored = [(p.payload["text"], float(p.score)) for p in points]

    bm25_scored = bm25_retriever.search_scored(query, top_k=top_k)

    fused = rrf_fusion(bm25_scored, vec_scored)
    return fused[:top_k]


async def _retrieve_one(query: str, collection: str, embed_model: str, bm25_retriever) -> list[str]:
    """Async wrapper for hybrid retrieval (runs sync code in thread pool)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _hybrid_retrieve, query, collection, embed_model, bm25_retriever
    )


async def _retrieve_news(query: str, embed_model: str, days: int = 30, top_k: int = 5) -> list[str]:
    """Query news_chunks with time-filter. Returns tagged chunks. Warns when 0 results.

    Uses NEWS_EMBED_MODEL env var (OLLAMA_EMBED_MODEL), NOT the hpg_chunks embed_model.
    news_chunks was indexed with its own model — mixing models causes dim mismatch.
    """
    from rag.news_index import search_news_by_text, classify_sentiment, DEFAULT_EMBED_MODEL as NEWS_EMBED_MODEL

    loop = asyncio.get_event_loop()
    payloads = await loop.run_in_executor(
        None, search_news_by_text, query, NEWS_EMBED_MODEL, days, top_k
    )
    if not payloads:
        print(f"  [news] WARN: 0 results for '{query[:40]}' (days={days}) — collection empty or filter broken")
        return []
    results = []
    seen_titles: set[str] = set()
    for p in payloads:
        date = str(p.get("published_at", ""))[:10]
        title = p.get("title", "")
        # A4: skip near-duplicate titles (same first 40 chars)
        title_key = title[:40].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        source = p.get("source", "")
        tickers = p.get("tickers", [])
        ticker_hint = f" [{', '.join(tickers)}]" if tickers else ""
        text = p.get("text", title)
        sentiment = classify_sentiment(text)
        results.append(f"[TIN TỨC {date} | sentiment: {sentiment}] {title}{ticker_hint} (nguồn: {source})")
    return results


# ── Nodes ──────────────────────────────────────────────────────────────────────

def make_decompose_node(n_sub_queries: int = 4):
    def decompose_node(state: FusionState) -> FusionState:
        query = state["query"]
        print(f"  [decompose] query='{query[:60]}...'")
        sub_queries = generate_sub_queries(query, n=n_sub_queries)
        print(f"  [decompose] {len(sub_queries)} sub-queries:")
        for i, sq in enumerate(sub_queries, 1):
            print(f"    {i}. {sq}")
        return {**state, "sub_queries": sub_queries}
    return decompose_node


def make_multi_retrieve_node(
    collection: str,
    embed_model: str,
    bm25_retriever,
    candidate_k: int = 10,
):
    def multi_retrieve_node(state: FusionState) -> FusionState:
        sub_queries = state.get("sub_queries", [state["query"]])
        ticker = state.get("ticker", "HPG")

        t0 = time.perf_counter()

        # Parallel async retrieval: hpg_chunks (all sub-queries) + news_chunks (top 2)
        async def _gather():
            tasks = [
                _retrieve_one(sq, collection, embed_model, bm25_retriever)
                for sq in sub_queries
            ]
            tasks += [
                _retrieve_news(sq, embed_model, days=30)
                for sq in sub_queries[:2]
            ]
            return await asyncio.gather(*tasks)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already inside an event loop (e.g. Jupyter) — use nest_asyncio
                import nest_asyncio
                nest_asyncio.apply()
                raw_results = loop.run_until_complete(_gather())
            else:
                raw_results = loop.run_until_complete(_gather())
        except RuntimeError:
            raw_results = asyncio.run(_gather())

        elapsed = time.perf_counter() - t0
        total_chunks = sum(len(r) for r in raw_results)
        print(f"  [multi_retrieve] {len(sub_queries)} queries × ~{candidate_k} chunks "
              f"= {total_chunks} total  ({elapsed:.2f}s parallel)")

        # Postgres structured data
        postgres_chunks: list[str] = []
        pg_facts = query_postgres_facts(ticker, limit=30)
        if pg_facts:
            pg_text = format_postgres_facts_as_text(pg_facts, ticker)
            postgres_chunks = [pg_text]
            print(f"  [multi_retrieve] Postgres: {len(pg_facts)} facts")

        # Web search (Tavily) — optional, skip gracefully if key missing
        web_chunks: list[str] = []
        tavily_key = os.environ.get("TAVILY_API_KEY", "")
        if tavily_key:
            try:
                from llm.factory import create_search_tool
                search_tool = create_search_tool(max_results=3)
                web_results = search_tool.invoke(state["query"])
                for item in web_results:
                    content = item.get("content", "")
                    url = item.get("url", "")
                    if content:
                        web_chunks.append(f"[WEB] {content}\nNguồn: {url}")
                print(f"  [multi_retrieve] Web: {len(web_chunks)} articles")
            except Exception as e:
                print(f"  [multi_retrieve] Web search skipped: {e}")

        all_results = list(raw_results) + ([postgres_chunks] if postgres_chunks else []) + ([web_chunks] if web_chunks else [])
        return {**state, "raw_results": all_results}

    return multi_retrieve_node


def make_rrf_fuse_node(top_k: int = 5):
    def rrf_fuse_node(state: FusionState) -> FusionState:
        raw_results = state.get("raw_results", [])
        if not raw_results:
            return {**state, "fused_chunks": [], "sources_used": []}

        # Convert each result list to scored pairs (rank-only, score=1.0 placeholder)
        # rrf_fusion expects list[tuple[str, float]]
        scored_lists = [
            [(chunk, 1.0) for chunk in result_list]
            for result_list in raw_results
            if result_list
        ]

        if not scored_lists:
            return {**state, "fused_chunks": [], "sources_used": []}

        # Multi-list RRF: iteratively fuse pairs
        if len(scored_lists) == 1:
            fused = [chunk for chunk, _ in scored_lists[0]]
        else:
            # Fold: fuse first two, then fuse result with third, etc.
            current_fused_scored = scored_lists[0]
            for next_list in scored_lists[1:]:
                merged_texts = rrf_fusion(current_fused_scored, next_list)
                current_fused_scored = [(t, 1.0) for t in merged_texts]
            fused = [chunk for chunk, _ in current_fused_scored]

        top_chunks = fused[:top_k]

        # Detect source labels
        sources = []
        for chunk in top_chunks:
            if chunk.startswith("[BCTC"):
                if "BCTC" not in sources:
                    sources.append("BCTC")
            elif chunk.startswith("[TIN TỨC"):
                if "TIN TỨC" not in sources:
                    sources.append("TIN TỨC")
            elif chunk.startswith("[WEB]"):
                if "WEB" not in sources:
                    sources.append("WEB")
            elif chunk.startswith("[GIÁ LỊCH SỬ"):
                if "GIÁ LỊCH SỬ" not in sources:
                    sources.append("GIÁ LỊCH SỬ")
            else:
                if "RAG corpus" not in sources:
                    sources.append("RAG corpus")

        print(f"  [rrf_fuse] {len(fused)} unique chunks → top {len(top_chunks)}  sources: {sources}")
        return {**state, "fused_chunks": top_chunks, "sources_used": sources}

    return rrf_fuse_node


def make_analyze_node():
    def analyze_node(state: FusionState) -> FusionState:
        query = state["query"]
        fused_chunks = state.get("fused_chunks", [])
        if not fused_chunks:
            return {**state, "analysis": "Không tìm thấy thông tin liên quan trong tài liệu."}

        context_block = "\n\n---\n\n".join(fused_chunks)
        system = (
            "Bạn là chuyên gia phân tích tài chính doanh nghiệp.\n\n"
            "GUARD 1 — đầu tư: TUYỆT ĐỐI không đưa ra lời khuyên mua, bán, "
            "hoặc nắm giữ bất kỳ cổ phiếu nào. Nếu người dùng yêu cầu, từ chối rõ ràng.\n\n"
            "GUARD 2 — nguồn và thời gian:\n"
            "- Mỗi đoạn [BCTC ...] là dữ liệu báo cáo tài chính chính thức.\n"
            "- Mỗi đoạn [TIN TỨC YYYY-MM-DD] là tin tức nội bộ đã được thu thập và lưu trữ.\n"
            "- Mỗi đoạn [WEB] là kết quả tìm kiếm web thời gian thực (Tavily) — chưa được kiểm chứng.\n"
            "- Khi dùng [WEB]: PHẢI ghi tag '(Nguồn: Web)' sau thông tin, và thêm ghi chú 'Chưa được kiểm chứng'.\n"
            "- Khi đưa ra số liệu hoặc sự kiện từ BCTC/TIN TỨC, PHẢI ghi rõ nguồn: '(BCTC 2025)' hoặc '(tin ngày DD/MM)'.\n"
            "- Khi các nguồn mâu thuẫn nhau: KHÔNG chọn im lặng — phải ghi "
            "'Các nguồn không thống nhất: [nguồn A] cho rằng X, [nguồn B] cho rằng Y'.\n"
            "- Khi có nhiều bài tin về cùng chủ đề: ưu tiên bài mới hơn, nhưng ghi nhận nếu thông tin thay đổi.\n"
            "- Không gọi tin tức là 'gần đây' nếu bài cũ hơn 14 ngày — ghi rõ ngày thay thế.\n\n"
            "Nhiệm vụ: phân tích thông tin từ các đoạn tài liệu bên dưới để trả lời câu hỏi. "
            "Chỉ dựa vào tài liệu được cung cấp. Nếu thông tin không đủ, nói rõ.\n\n"
            "Trả lời trực tiếp bằng tiếng Việt. Không giải thích quá trình suy nghĩ.\n\n"
            f"TÀI LIỆU:\n{context_block}"
        )
        client = create_client()
        resp = client.generate(
            [Message(role="user", content=query)],
            max_tokens=1024,
            system=system,
        )
        return {**state, "analysis": resp.text}

    return analyze_node


def make_report_node():
    def report_node(state: FusionState) -> FusionState:
        analysis = state.get("analysis", "")
        sources_used = state.get("sources_used", [])
        sub_queries = state.get("sub_queries", [])

        sources_str = ", ".join(sources_used) if sources_used else "RAG corpus"
        sub_q_str = "\n".join(f"  - {sq}" for sq in sub_queries)

        report = (
            f"{analysis}\n\n"
            f"---\n"
            f"**Nguồn dữ liệu:** {sources_str}\n"
            f"**Sub-queries dùng để tìm kiếm ({len(sub_queries)}):**\n{sub_q_str}"
        )
        return {**state, "report": report}

    return report_node


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_rag_fusion_graph(
    collection: str,
    embed_model: str,
    bm25_retriever,
    n_sub_queries: int = 4,
    candidate_k: int = 10,
    top_k: int = 5,
):
    """Build and compile the RAG-Fusion LangGraph."""
    graph = StateGraph(FusionState)

    graph.add_node("decompose",     make_decompose_node(n_sub_queries))
    graph.add_node("multi_retrieve", make_multi_retrieve_node(collection, embed_model, bm25_retriever, candidate_k))
    graph.add_node("rrf_fuse",      make_rrf_fuse_node(top_k))
    graph.add_node("analyze",       make_analyze_node())
    graph.add_node("report",        make_report_node())

    graph.set_entry_point("decompose")
    graph.add_edge("decompose",      "multi_retrieve")
    graph.add_edge("multi_retrieve", "rrf_fuse")
    graph.add_edge("rrf_fuse",       "analyze")
    graph.add_edge("analyze",        "report")
    graph.add_edge("report",         END)

    return graph.compile()


def run_rag_fusion(
    query: str,
    collection: str,
    embed_model: str,
    bm25_retriever,
    ticker: str = "HPG",
    n_sub_queries: int = 4,
    candidate_k: int = 10,
    top_k: int = 5,
) -> dict:
    """Run the full RAG-Fusion pipeline for a single query. Returns final state."""
    app = build_rag_fusion_graph(collection, embed_model, bm25_retriever, n_sub_queries, candidate_k, top_k)
    result = app.invoke({"query": query, "ticker": ticker})
    return result
