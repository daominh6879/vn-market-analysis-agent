# Bài 15 — So sánh tất cả collections + eval keyword-based (2026-08-25)

**Script:** `evals/eval_all_collections.py` → `evals/all_collections_eval.json`  
**Golden set:** `evals/golden_hpg.yaml` — 21 câu có ground truth + 5 refusal (q21–q25)  
**Phương pháp eval:** keyword matching (không dùng LLM), MAP + hit@k

---

## Collections được so sánh (6 collections, top_k=5)

| Collection | Chunking | Metadata |
|---|---|---|
| hpg_b7_fixed_nometa | fixed 512 | không |
| hpg_b7_structural_nometa | structural | không |
| hpg_b7_hier_nometa | hierarchical | không |
| hpg_b7_fixed_meta | fixed 512 | có |
| hpg_b7_structural_meta | structural | có |
| hpg_b7_hier_meta | hierarchical | có |

---

## Kết quả — Vector retriever

| Collection | context_recall | context_precision (MAP) | faithfulness | ans_relevancy |
|---|---|---|---|---|
| hpg_b7_fixed_nometa | 0.333 | 0.178 | 0.333 | 0.095 |
| hpg_b7_structural_nometa | 0.333 | 0.198 | 0.333 | 0.095 |
| hpg_b7_hier_nometa | 0.286 | 0.182 | 0.286 | 0.095 |
| hpg_b7_fixed_meta | 0.333 | 0.188 | 0.333 | 0.143 |
| **hpg_b7_structural_meta** | **0.524** | **0.255** | **0.524** | 0.095 |
| hpg_b7_hier_meta | 0.429 | 0.243 | 0.429 | 0.143 |

## Kết quả — BM25 retriever

| Collection | context_recall | context_precision (MAP) | faithfulness | ans_relevancy |
|---|---|---|---|---|
| hpg_b7_fixed_nometa | 0.381 | 0.239 | 0.381 | 0.190 |
| **hpg_b7_structural_nometa** | **0.524** | **0.295** | **0.524** | 0.143 |
| hpg_b7_hier_nometa | 0.381 | 0.236 | 0.381 | 0.143 |
| hpg_b7_fixed_meta | 0.381 | 0.239 | 0.381 | 0.190 |
| **hpg_b7_structural_meta** | **0.524** | **0.295** | **0.524** | 0.143 |
| hpg_b7_hier_meta | 0.381 | 0.236 | 0.381 | 0.143 |

## Kết quả — BM25-VN retriever

| Collection | context_recall | context_precision (MAP) | faithfulness | ans_relevancy |
|---|---|---|---|---|
| hpg_b7_fixed_nometa | 0.286 | 0.218 | 0.286 | 0.190 |
| **hpg_b7_structural_nometa** | **0.429** | **0.287** | **0.429** | 0.190 |
| hpg_b7_hier_nometa | 0.286 | 0.202 | 0.286 | 0.143 |
| hpg_b7_fixed_meta | 0.286 | 0.218 | 0.286 | 0.190 |
| **hpg_b7_structural_meta** | **0.429** | **0.287** | **0.429** | **0.190** |
| hpg_b7_hier_meta | 0.286 | 0.202 | 0.286 | 0.143 |

---

## Định nghĩa metrics (keyword-based, không LLM)

- **context_recall** = hit@5 rate — answer keyword xuất hiện trong top-5 contexts
- **context_precision** = MAP (Mean Average Precision) — rank của chunk đúng trong top-5
- **faithfulness** = proxy bằng context_recall — nếu context không có đáp án, model dễ hallucinate
- **answer_relevancy** = proxy bằng hit@1 rate — chunk rank-1 có keyword = câu trả lời likely on-topic
- **refusal_pass_rate** = không tính được — q21–q25 không có trong `all_collections_eval.json`

---

## Quyết định: chọn `hpg_b7_structural_meta` + vector

**Lý do:**
- recall 0.524 — cao nhất trong vector (so với 0.333 fixed, 0.286 hier)
- MAP 0.255 — cao nhất trong vector
- Meta giúp vector tăng recall +57% so với structural_nometa (0.524 vs 0.333)
- BM25 không hưởng lợi từ meta (structural_meta = structural_nometa = 0.524) — meta chỉ ảnh hưởng embedding

**Hybrid tốt nhất:** hpg_b7_structural_meta vector (0.524) + BM25 cùng collection (0.524) → union top-5 → rerank.

---

## Quan sát

**structural > fixed > hier** nhất quán trên cả 3 retrievers — chunking theo cấu trúc tài liệu giữ nguyên bảng và đoạn văn liên quan.

**table_lookup vẫn yếu:** q31, q33, q35, q36, q39 miss hoàn toàn cả 3 retrievers. Số VND cụ thể nằm trong bảng HTML bị tách chunk → cần xử lý bảng riêng ở bài tiếp theo.

**context_precision (MAP) thấp (0.255–0.295):** khi tìm được answer, chunk đúng thường ở rank 3–5 chứ không phải rank 1 → cần reranker.

**refusal_pass_rate:** chưa eval — q21–q25 không có trong eval JSON hiện tại.
