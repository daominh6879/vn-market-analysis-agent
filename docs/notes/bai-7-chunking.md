# Bài 7 — So sánh chunking (2026-08-18)

**Config:** nomic-embed-text, RAGAS judge DeepSeek, dữ liệu `outputs/hpg_pymupdf.md` (93,511 chars)

## Số chunk & index time

| Chiến lược | Chunks | Index time |
|---|---|---|
| fixed_512 | 260 | 615s |
| structural | 166 | 398s |
| hierarchical (child) | 388 | 871s |

## RAGAS scores

| Chiến lược | faithfulness | answer_relevancy | context_precision | context_recall | refusal_pass_rate |
|---|---|---|---|---|---|
| fixed_512 | **0.931** | 0.091 | 0.075 | **0.100** | **1.000** |
| structural | 0.615 | 0.119 | 0.095 | 0.071 | **1.000** |
| hierarchical | 0.867 | **0.128** | **0.100** | **0.100** | 0.800 |

## Metadata experiment (fixed_512)

| | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| no metadata | **0.931** | 0.091 | 0.075 | 0.100 |
| `[HPG\|2025\|BCTC_rieng_le]` prepend | 0.704 | **0.123** | 0.075 | 0.100 |

Metadata giảm faithfulness −0.227. Vector query không có prefix → cosine bị lệch. Với 1 file đơn nguồn, metadata là nhiễu thuần.  
→ **Bật metadata lại sau Bài 12** khi index nhiều ticker/năm.

**Lý do scores thấp tổng thể:** q01–q07, q14–q17 lấy từ XLS hợp nhất, không có trong PDF đã index. Chỉ ~8-10 câu thật sự answerable từ file hiện tại.

**Quyết định ban đầu (1 file):** Dùng **fixed_512, không metadata** → bị bác bỏ sau re-run 2 file bên dưới.

---

## Bài 7 Re-run — 2 file (2024 + 2025), 6 collections (2026-08-20)

**Config:** bge-m3, RAGAS judge DeepSeek, dữ liệu `outputs/hpg_pymupdf.md` + `outputs/2024/hpg_pymupdf.md`  
**Golden set:** 11 câu `indexed=true` (q08–q13 từ 2025 PDF, q26–q30 từ 2024 PDF) + 5 câu refusal  
**Script:** `python evals/compare_chunking.py --full-ragas`  
**Output:** `evals/chunking_compare_b7_rerun.json`

### RAGAS scores (6 collections)

| Collection | refusal | faithfulness | answer_relevancy | context_precision | context_recall | avg (4) |
|---|---|---|---|---|---|---|
| fixed_512 \| no meta | 0.800 | **1.000** | 0.212 | 0.420 | 0.409 | 0.510 |
| fixed_512 \| meta | **1.000** | 0.000 | 0.000 | 0.000 | 0.000 | **0.000** |
| structural \| no meta | **1.000** | **1.000** | **0.446** | 0.476 | **0.636** | **0.640** |
| structural \| meta | **1.000** | **1.000** | 0.321 | **0.598** | 0.545 | 0.616 |
| hierarchical \| no meta | **1.000** | **1.000** | 0.341 | 0.473 | 0.455 | 0.567 |
| hierarchical \| meta | 0.800 | 0.571 | 0.343 | 0.495 | **0.636** | 0.511 |

### Phân tích metadata với 2 file

| Chiến lược | faithfulness Δ | answer_relevancy Δ | context_precision Δ | context_recall Δ |
|---|---|---|---|---|
| fixed_512 | **−1.000** (sập hoàn toàn) | −0.212 | −0.420 | −0.409 |
| structural | 0.000 | −0.125 | **+0.122** | −0.091 |
| hierarchical | −0.429 | +0.002 | +0.022 | **+0.181** |

**fixed_512 meta = sập hoàn toàn:** prefix `[HPG|year|...]` trong chunk, query không có prefix → cosine similarity về 0 → không retrieve được gì → LLM từ chối tất cả eval_qs → RAGAS = 0.

**structural meta:** precision tăng (+0.122) nhưng recall và answer_relevancy giảm → net âm.

**Kết luận về metadata:** Hại cho cả 3 chiến lược khi đo avg (4 metrics). Không bật metadata cho đến Bài 12.

### Kết luận thay đổi so với Bài 7 ban đầu

fixed_512 thắng với 1 file → **structural thắng với 2 file** (avg RAGAS 0.640 vs 0.510). Structural chunk theo ranh giới tự nhiên của tài liệu → giữ nguyên context đầy đủ hơn khi nhiều nguồn.

**Quyết định cập nhật:** Dùng **structural, không metadata**.  
**Pipeline hiện tại: structural + bge-m3 + không metadata**
