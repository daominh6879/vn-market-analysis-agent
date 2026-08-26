# Kiến trúc hệ thống RAG — ai-engineer

> Cập nhật sau mỗi bài. Ghi rõ bài nào thêm/thay đổi gì ở cuối file.

---

## Tổng quan

RAG pipeline cho tài liệu tài chính tiếng Việt (HPG BCTC). Kết hợp báo cáo tài chính PDF, tin tức thời gian thực, và số liệu có cấu trúc từ Postgres để trả lời câu hỏi phân tích tài chính.

---

## Stack

| Layer | Công nghệ |
|---|---|
| LLM | DeepSeek (default), Ollama local (fallback) |
| Vector store | Qdrant |
| Relational DB | Postgres |
| Object storage | MinIO |
| Pipeline orchestration | Dagster |
| RAG graph | LangGraph |
| Web search | Tavily |
| Financial data API | vnstock |
| Embedding | nomic-embed-text (Ollama) |
| Eval | RAGAS + golden YAML |
| Reranker | CrossEncoder (cross-encoder/ms-marco-MiniLM-L-6-v2) |

---

## Nguồn dữ liệu

| Nguồn | Nội dung | Lưu ở đâu | Tag trong context |
|---|---|---|---|
| PDF BCTC (HPG) | Báo cáo tài chính đã kiểm toán | Qdrant `hpg_b7_structural_meta` | `RAG corpus` |
| CafeF / VnExpress RSS | Tin tức tài chính, thép, TTCK | Postgres `news_articles` + Qdrant `news_chunks` | `TIN TỨC` |
| vnstock Finance API | KQKD, CDKT, LCTT (annual) | Postgres `financial_facts` (source='vnstock') | `GIÁ LỊCH SỬ` |
| vnstock Prices | Giá đóng cửa điều chỉnh hàng ngày | Postgres `stock_prices` | `GIÁ LỊCH SỬ` |
| LLM extract từ PDF | Số liệu tài chính (fallback) | Postgres `financial_facts` (source='pdf') | `GIÁ LỊCH SỬ` |
| Tavily web search | Kết quả tìm kiếm thời gian thực | Không lưu (real-time) | `WEB` |

**Priority khi trùng `financial_facts`:** vnstock > pdf (ON CONFLICT DO UPDATE).

---

## Dagster Pipeline

```
Group: ingestion (PDF BCTC)
  raw_pdf → parsed_doc → embeddings      → Qdrant hpg_b7_structural_meta
                       → financial_facts  → Postgres (source='pdf')

Group: news (mỗi 6h)
  news_raw → news_indexed  → Qdrant news_chunks
  news_purge               → xóa > 90 ngày (Chủ Nhật 02:00)

Group: vnstock
  vnstock_financials       → Postgres financial_facts (mùng 1 hàng tháng 01:00)
  vnstock_prices           → Postgres stock_prices (18:00 ngày thường)
```

**Sensor:** `minio_new_pdf_sensor` — phát hiện PDF mới/sửa/xóa trong MinIO mỗi 5 phút.

---

## RAG-Fusion Graph (LangGraph)

```
decompose → multi_retrieve → rrf_fuse → analyze → report
```

| Node | Việc làm |
|---|---|
| `decompose` | LLM sinh N sub-queries từ câu hỏi gốc (default N=4) |
| `multi_retrieve` | Parallel: BM25 + vector hybrid (hpg_chunks) + news_chunks + Postgres facts + Tavily web |
| `rrf_fuse` | RRF merge tất cả kết quả → top K chunks, gắn `sources_used` |
| `analyze` | LLM đọc fused context → trả lời, gắn tag nguồn trong text |
| `report` | Format output + citation |

### Retrieval trong `multi_retrieve`

```
hpg_chunks:   BM25 (underthesea VN tokenize) + vector (Qdrant) → RRF → top 20
news_chunks:  vector search + DatetimeRange filter (30 ngày) → classify_sentiment() on-the-fly
Postgres:     query_postgres_facts() → financial_facts
Web:          Tavily.invoke(query) → 3 articles
```

### Sentiment trong news_chunks

Sentiment **không** được lưu vào Qdrant payload (quá đắt khi index hàng loạt).  
Thay vào đó, `_retrieve_news()` gọi `classify_sentiment()` trên mỗi chunk vừa lấy về (~5 calls/query):

```
_retrieve_news() → top 5 payloads → classify_sentiment(payload["text"]) → tag vào chunk
→ "[TIN TỨC 2026-08-20 | sentiment: negative] Thép giảm mạnh (nguồn: cafef.vn)"
```

`analyze_node` thấy tag → tổng hợp được: "3/5 tin negative, 1 neutral, 1 positive".

---

## Chiến lược Chunking

| Collection | Chiến lược | Chunk size | Ghi chú |
|---|---|---|---|
| `hpg_b7_structural_meta` | structural + metadata | ~512 token | Dùng trong production |
| `hpg_fixed_512` | fixed 512 | 512 token | Baseline |
| `news_chunks` | title + body | N/A | Mỗi article = 1 chunk |

---

## Retrieval Methods (eval modes)

| `--retriever` | Mô tả |
|---|---|
| `vector` | Chỉ Qdrant vector search |
| `bm25` | Chỉ BM25 |
| `hybrid_weighted` | BM25 + vector, weighted sum fusion |
| `hybrid_rrf` | BM25 + vector, RRF fusion |
| `hybrid_rerank` | hybrid_weighted → CrossEncoder rerank |
| `rag_fusion` | Decompose → multi-retrieve → RRF → LangGraph (production) |
| `router_sql` | Classify → SQL agent hoặc hybrid_rerank |

---

## Router + SQL Agent

```
câu hỏi → classify (LLM) → label
  số_liệu     → SQL agent → financial_facts / stock_prices
  diễn_giải   → hybrid_rerank → RAG corpus
  cả_hai      → SQL + RAG → LLM combine
  ngoài_phạm_vi → từ chối
```

**Allowed tables:** `financial_facts`, `stock_prices`.

---

## Source Tags trong Context

| Tag | Nguồn | Ghi chú |
|---|---|---|
| `RAG corpus` | hpg_chunks (PDF embed) | Không có prefix trong raw text |
| `TIN TỨC YYYY-MM-DD \| sentiment: X` | news_chunks nội bộ | Sentiment classify lúc retrieve (không lưu Qdrant) |
| `GIÁ LỊCH SỬ` | Postgres financial_facts + stock_prices | vnstock hoặc PDF extract |
| `WEB` | Tavily real-time | Chưa kiểm chứng — LLM phải ghi `(Nguồn: Web)` |

---

## Eval

| File | Mô tả |
|---|---|
| `evals/golden_hpg.yaml` | Golden questions: table_lookup, text_interpretation, no_answer, out_of_scope, news |
| `evals/run.py` | Runner: model call → RAGAS score → regression check |
| `evals/eval_rag_fusion.py` | Eval riêng RAG-Fusion pipeline |
| `evals/eval_router.py` | Eval router accuracy |

**Groups trong golden YAML:**
- `table_lookup` / `text_interpretation` → RAGAS (faithfulness, relevancy, precision, recall)
- `no_answer` / `out_of_scope` → refusal pass rate
- `news` → pass/fail dựa trên `sources_used` có `TIN TỨC`

**Threshold regression:** drop > 0.1789 (2×std từ noise measurement) → CI fail.

---

## Postgres Schema (chính)

```sql
financial_facts (ticker, period, report_type, metric_code, value, unit, source_file, source_page, source)
  UNIQUE (ticker, period, report_type, metric_code)

stock_prices (ticker, ngay, close_adj, volume)
  UNIQUE (ticker, ngay)

news_articles (url, title, body, source, published_at, tickers[], indexed_at)
  UNIQUE (url)
```

---

## Changelog

| Bài | Thay đổi |
|---|---|
| 1–12 | Ingestion PDF, chunking, embedding, BM25, hybrid, reranker, facts SQL, news scraper |
| 13 | Dagster pipeline: ingestion_job, news_job, sensor MinIO |
| 14 | Router + SQL agent |
| 15–16 | RAG-Fusion (LangGraph): decompose → multi_retrieve → RRF → analyze |
| 17 | Tenant isolation |
| 18 | Router + SQL tích hợp vào rag_fusion |
| — | Thêm Dagster assets vnstock_financials + vnstock_prices |
| — | Phân biệt source tags: WEB vs TIN TỨC vs RAG corpus |
| — | Sentiment classify at retrieve time (không lưu Qdrant payload) — `classify_sentiment()` trong `_retrieve_news()` |
