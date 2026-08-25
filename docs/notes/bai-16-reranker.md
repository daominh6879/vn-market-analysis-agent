# Bài 16 — Reranker + Bảng số hoàn chỉnh

**Pipeline:** fusion weighted_sum (candidate_k=30) → CrossEncoder BAAI/bge-reranker-v2-m3 → top-5  
**Script:** `evals/eval_reranker.py` + `evals/run.py --retriever hybrid_rerank`

---

## Kết luận

**Fusion_ws (bài 15) vẫn là best retriever. Reranker không cải thiện pipeline này.**

> *"Với structural chunks trên BCTC tiếng Việt + mDeBERTa trên CPU, reranker không giúp được."*

---

## Kiến trúc hai tầng

```
Query
  │
  ├─ BM25 vn ──────────┐
  │                    ├─ weighted_sum_fusion → top-20 candidates
  └─ Vector (bge-m3) ──┘
                              │
                     CrossEncoder rerank (query × doc)
                              │
                         top-5 → LLM
```

**Tại sao CrossEncoder chính xác hơn bi-encoder (lý thuyết):**
- Bi-encoder: mã hoá query và doc *riêng lẻ* → vector, so cosine. Mất thông tin tương tác.
- CrossEncoder: đọc (query + doc) *cùng lúc* → mỗi token query attend đến mỗi token doc → hiểu relevance trực tiếp.

**Tại sao không rerank toàn corpus:**
~800 chunks × 15s/20-pairs = không dùng được real-time. Giải pháp hai tầng: retrieval broad → rerank narrow.

---

## Kết quả eval (collection: hpg_structural, candidate_k=30, top_k=5)

### Tổng hợp

| Strategy | hit@5 | p95 | So với fusion |
|---|---|---|---|
| fusion_ws (bài 15) | **13/21** | ~5s | baseline |
| reranker_256 (max_length=256) | 11/21 | 17.6s | **-2, +12.6s** |
| reranker_snippet (256 + extract) | 10/21 | 20.7s | **-3, +15.7s** |

### Per-group hit@5

| Group | fusion_ws | reranker_256 | reranker_snippet |
|---|---|---|---|
| table_lookup (10 câu) | **6/10** | 3/10 (-3) | 3/10 (-3) |
| text_interpretation (11 câu) | 7/11 | **8/11 (+1)** | 7/11 (=) |

---

## Phân tích thất bại

### Timing — max_length=256 chỉ tiết kiệm 40%, không phải 4×

```
max_length=512 → 26s/query
max_length=256 → 15.7s/query  (kỳ vọng ~6s)
```

Root cause: `bge-reranker-v2-m3` dùng **mDeBERTa-v3** (184M params, disentangled attention) — tốn gấp đôi memory bandwidth so với BERT. Sequence length không phải bottleneck duy nhất. Không GPU → không fix được.

### Quality regression — table_lookup

Structural chunk = **markdown table section** (~450 tokens, 30-50 dòng):
```
| A. TÀI SẢN NGẮN HẠN | 100 | 97.018.349.440.000 | 80.585.847.420.000 |
| I. Tiền tương đương  | 110 | 14.347.362.462.056 | 10.247.400.472.100 |
... 48 dòng nữa ...
```

CrossEncoder chấm điểm `(query, bảng 50 dòng)` → tín hiệu relevance bị pha loãng bởi 49 dòng không liên quan → score thấp. BM25 không bị vấn đề này (chỉ đếm token khớp, không bị nhiễu bởi context).

Snippet extraction không cứu được: `extract_snippet()` chọn dòng khớp token nhiều nhất → thường chọn dòng header (`## Bảng cân đối kế toán`) thay vì dòng số.

### Partial win — text_interpretation

reranker_256 +1 so với fusion cho text_interpretation (8/11 vs 7/11). Chunks dạng prose phù hợp hơn với cross-encoder. Nhưng gain nhỏ không bù được loss ở table_lookup.

---

## Bảng số hoàn chỉnh 5 dòng

| Cấu hình | Đoạn đúng trong top-5 | Ghi chú | p95 |
|---|---|---|---|
| Model trần, không RAG | ~2-3/21 | Hallucinate số liệu tài chính | ~2s |
| Chỉ vector (bge-m3) | 7/21 | Baseline retrieval | ~2.5s |
| + BM25, fusion weighted_sum | **13/21** | **Winner** — +86% vs vector | ~5s |
| + reranker (CrossEncoder) | 11/21 | Kém hơn fusion, chậm 3× | ~20s |
| + filter metadata | *(bài 17)* | | |

**One-liner:**
> *"Fusion hit@5 tăng từ 7 lên 13 (+86%). Reranker không giúp thêm — structural table chunks và mDeBERTa không phù hợp nhau."*

---

## Nếu cần reranker trong tương lai

Điều kiện để reranker hoạt động tốt:
1. Chunks ngắn, focused (~100-200 tokens) — không phải full table section
2. Model nhẹ: `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params, ~50ms/pair CPU)
3. Chỉ rerank `text_interpretation` queries (cần router — bài 18)
4. GPU inference nếu cần production latency <500ms

---

## Lệnh chạy

```bash
# Eval 3 reranker variants (bỏ qua variant 512 đã biết kết quả)
uv run python evals/eval_reranker.py \
  --collection hpg_structural \
  --candidate-k 30 \
  --skip-512 \
  --out evals/reranker_results.json
```

---

## Lost-in-middle effect (implemented, không đo được impact riêng)

```python
# rerank_for_llm() trong rag/reranker.py
# ranked[0] = chunk điểm cao nhất → chuyển về cuối context
texts = texts[1:] + [texts[0]]
# Order → LLM: [rank2, rank3, rank4, rank5, rank1]
```

LLM chú ý nhiều hơn ở cuối context (Liu et al. 2023). Không đo được impact riêng trong eval này vì eval chấm retrieval, không chấm LLM output.
