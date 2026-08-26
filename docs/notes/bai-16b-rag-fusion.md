# Bài 16b — RAG-Fusion: Multi-Query + RRF

**Pipeline:** LLM sinh N sub-queries → hybrid retrieval song song → RRF fusion → LLM analyze  
**Scripts:** `rag/multi_query.py` · `rag/rag_fusion_graph.py` · `evals/eval_rag_fusion.py`

---

## Kết quả eval (collection: hpg_b7_structural_meta, N=4, top_k=5)

| Cấu hình | recall@5 | Thời gian p95 | Chi phí LLM thêm |
|---|---|---|---|
| Single query (hybrid RRF) | 0.857 (18/21) | 2.8s | — |
| Multi-query (N=4) + RRF | **0.952 (20/21)** | 8.1s | +1 LLM call/query (sub-query gen) |

**Câu thua (q29):** cả hai đều miss — thông tin không có trong corpus.  
**Câu multi thắng single:** q08, q31 (2 câu `table_lookup` single miss).

**One-liner:**
> *"Multi-query recall@5 tăng từ 0.857 lên 0.952 (+11%). Đổi lại thêm ~5s p95 và 1 LLM call mỗi câu hỏi."*

---

## Kiến trúc LangGraph (5 node)

```
query
  │
  ▼
[decompose] ── LLM sinh N sub-queries (4 góc: số liệu / so sánh / ngành / sự kiện)
  │
  ▼
[multi_retrieve] ── asyncio.gather: N × hybrid(BM25+vector) song song
  │                + Postgres financial_facts
  │                + Tavily web search (nếu có key)
  ▼
[rrf_fuse] ── fold RRF qua tất cả result lists → top_k chunks
  │
  ▼
[analyze] ── LLM đọc fused context + GUARD chống lời khuyên đầu tư
  │
  ▼
[report] ── format với bảng số + trích nguồn
```

---

## Bốn nguồn dữ liệu (sau bài 12B)

| Nguồn | Dùng cho | Tag | Retrieval |
|---|---|---|---|
| `hpg_chunks` (Qdrant) | BCTC đã index | `[BCTC period]` | BM25+vector hybrid, N=4 sub-queries |
| Postgres `financial_facts` | Số liệu vnstock | `[GIÁ LỊCH SỬ]` | Direct SQL |
| `news_chunks` (Qdrant) | Tin tức indexed 30 ngày | `[TIN TỨC date]` | Vector+DatetimeRange, top 2 sub-queries, không BM25 |
| Tavily web | Tin tức realtime | `[TIN TỨC date]` | API call nếu có key |

`news_chunks` không dùng BM25: news summary ngắn (200–800 chars), time-filter + vector đủ.
`news_chunks` embed model: `OLLAMA_EMBED_MODEL` (nomic-embed-text, dim=768) — khác `hpg_chunks` (bge-m3, dim=1024). Collection riêng → dim khác OK. RRF dùng rank không dùng score → cross-collection fusion an toàn.

---

## Guard đầu tư — hoạt động

```python
# rag/rag_fusion_graph.py — analyze_node system prompt
"TUYỆT ĐỐI không đưa ra lời khuyên mua, bán, hoặc nắm giữ bất kỳ cổ phiếu nào."
```

Test: câu "Tôi có nên mua cổ phiếu HPG không?" → model từ chối rõ ràng. Guard triggered: YES ✓

---

## Vấn đề kỹ thuật gặp phải

### DeepSeek trả về 1 sub-query thay vì 4
- **Root cause A:** `msg.content` chứa `<think>...</think>` + JSON → regex stripping không bắt được.
- **Root cause B:** Model trả về `["query_gốc"]` (1 phần tử, valid JSON) vì coi query là đủ đơn giản.
- **Root cause C:** Model trả về JSON + prose phía sau → `json.loads` fail → fallback.
- **Fix:** Bracket extraction (find `[` → matching `]`), few-shot example trong prompt, ép N phần tử bắt buộc.

### Analysis trống (`resp.text = ""`)
- **Root cause:** `deepseek-chat` với thinking mode trả về nội dung trong `reasoning_content`, `content = None`.
- **Fix:** `openai_client.py` — fallback sang `reasoning_content` + strip `<think>` regex.

---

## Kết luận chặng 3 (Bài 14–16b)

**Multi-query RAG-Fusion là retriever tốt nhất.** recall@5 = 0.952, nhưng đắt nhất (~8s + 2 LLM calls/query).

| Ưu tiên | Retriever | recall@5 | p95 |
|---|---|---|---|
| Chất lượng tối đa | RAG-Fusion N=4 | **0.952** | 8.1s |
| Balance chất lượng/tốc độ | Hybrid weighted_sum | ~0.619 (13/21) | ~5s |
| Tốc độ | Single vector | ~0.333 (7/21) | ~2.5s |

**Reranker loại khỏi danh sách** — chậm hơn fusion (20s p95) mà hit@5 kém hơn (11/21 vs 13/21).

**Root cause của mọi gain:** corpus HPG là BCTC dạng bảng → BM25 (khớp từ khoá số liệu) + multi-query (bắt nhiều góc nhìn) bổ sung nhau. Vector đơn không đủ vì bảng số không có ngữ nghĩa phong phú.

---

## Lệnh chạy

```bash
# Demo 4 câu HPG + guard test
python rag/demo_rag_fusion.py

# Eval so sánh single vs multi-query
python evals/eval_rag_fusion.py

# Tích hợp vào run.py
python evals/run.py --collection hpg_b7_structural_meta --embed bge-m3 \
  --retriever rag_fusion --skip-ragas
```
