# Bài 8 — So sánh embedding model (2026-08-18)

**Config:** fixed_512 (winner Bài 7), RAGAS judge DeepSeek  
**Script:** `evals/compare_embeds.py` → output `evals/embed_compare.json`

## RAGAS scores

| Metric | nomic-embed-text | bge-m3 | mxbai-embed-large |
|---|---|---|---|
| faithfulness | 0.722 | **0.976** | 0.765 |
| answer_relevancy | 0.128 | **0.143** | 0.103 |
| context_precision | 0.100 | **0.185** | 0.025 |
| context_recall | 0.100 | **0.200** | 0.050 |
| refusal_pass_rate | 1.000 | 1.000 | 1.000 |
| **avg RAGAS** | 0.262 | **0.376** | 0.236 |

## Index stats

| Model | Dims | Chunks | Index time |
|---|---|---|---|
| nomic-embed-text | 768 | 260 | 595s |
| bge-m3 | 1024 | 260 | 734s (+23%) |
| mxbai-embed-large | 1024 | 260 | 674s |

**Nhận xét:**
- bge-m3 thắng tất cả 4 metrics, chênh faithfulness 0.254 > ngưỡng nhiễu 0.1789 → thật
- context_recall gấp đôi nomic (0.200 vs 0.100)
- mxbai tệ hơn nomic trên precision/recall — multilingual ≠ tốt cho tiếng Việt tài chính
- Score tuyệt đối vẫn thấp: vấn đề dữ liệu thiếu (XLS), không phải embedding

**Quyết định:** Dùng **bge-m3**. Cập nhật `.env`: `OLLAMA_EMBED_MODEL=bge-m3`

**Pipeline hiện tại: structural + bge-m3 + không metadata**
