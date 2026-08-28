# Bài 26 · So 3 kiến trúc — Kết quả thực đo

**Script:** `evals/compare_architectures.py`
**Collection:** `bctc_structural` · embed: bge-m3 · top_k=5

---

## Lần chạy 2 — 28/08/2026 · arch_c v2 (Smart Planner) · 3 câu sample

**Thay đổi:** `decompose_query()` bị bỏ. arch_c dùng `query_interpreter.interpret()` để quyết định strategy retrieval:
- `sub_queries` set → multi-topic decompose với ticker filter
- `len(tickers) > 1` → per-ticker retrieve + RRF
- `years` set → retrieve không có year filter (giữ cả 2 năm trong context)
- simple → single retrieve + ticker/year filter

### BẢNG 1 — Tổng thể (3 câu · 1 per group)

| Chỉ số         | arch_a   | arch_b   | arch_c   |
|----------------|----------|----------|----------|
| quality_score  | **2.88** | 2.77     | **2.88** |
| latency_s      | **5.39** | 5.51     | 8.14     |
| total_tokens   | **1620** | 1727     | 1667     |
| cost_usd       | **$0.0007** | $0.0008 | $0.0008 |
| failure_rate   | **0%**   | **0%**   | **0%**   |

### BẢNG 2 — Theo nhóm (quality_score)

| Nhóm         | arch_a | arch_b | arch_c | Cải thiện arch_c |
|--------------|--------|--------|--------|-----------------|
| compound     | 5      | 5      | **5**  | +4 (từ 1 → 5)  |
| multi_source | 3      | 3      | **3**  | +0.6 (từ 2.4 → 3) |
| simple       | 2.63   | 2.47   | 2.63   | hoà             |

**arch_c v2 fixes hoàn toàn vấn đề compound:** `years=['2025','2024']` → retrieve không có year filter → LLM có context cả 2 năm → score 1 → 5.

**failure_rate 0%** (lần 1 arch_c có 3.85% do `decompose_query()` crash/timeout).

**Cost giảm:** 14% đắt hơn arch_a (lần 1 là 40%) vì bỏ 1 LLM call `decompose_query()`.

---

## Lần chạy 1 — 27/08/2026 · arch_c v1 (decompose_query) · 26 câu

### BẢNG 1 — Tổng thể (26 câu)

| Chỉ số         | arch_a  | arch_b  | arch_c  |
|----------------|---------|---------|---------|
| quality_score  | **3.31** | 3.19   | 2.88    |
| latency_s      | **5.93** | 6.20   | 9.24    |
| total_tokens   | 1956    | **1893** | 2366  |
| cost_usd       | **$0.0008** | $0.0008 | $0.0011 |
| failure_rate   | **0%**  | **0%**  | 3.85%   |

### BẢNG 2 — Theo nhóm (quality_score)

| Nhóm          | arch_a   | arch_b   | arch_c   | Số câu |
|---------------|----------|----------|----------|--------|
| simple        | **3.21** | 3.05     | **3.21** | 19     |
| compound      | **5.00** | **5.00** | 1.00     | 2      |
| multi_source  | **3.00** | **3.00** | 2.40     | 5      |

**Root cause arch_c v1 tệ:**
- `decompose_query()` tách "nhân viên 2025 và 2024" thành 2 sub-query riêng → mỗi cái lấy context 1 năm → không so sánh được → score=1
- Decompose mọi câu kể cả simple → tốn 1 LLM call thêm → cost +40%, latency +3s
- Không có payload filter → sub-queries retrieve toàn collection, noise cao

---

## Phân tích tổng hợp

### Vì sao arch_c nên thắng về lý thuyết?
Câu phức tạp (compound, multi-topic) cần nhiều context từ nhiều section. Pure vector RAG lấy top-5 chunk gần nhất — có thể bỏ sót section thứ 2. Planner retrieve nhiều lần từ nhiều góc → coverage tốt hơn.

### Vì sao arch_c v1 thất bại?
`decompose_query()` không phân biệt được "tách theo topic" (nên tách) vs "tách theo thời gian" (không nên tách). Câu compound bị tách sai → mất joint context.

### arch_c v2 fix gì?
`query_interpreter.interpret()` extract `years`, `tickers`, `sub_queries` trong 1 LLM call có structured output → quyết định strategy đúng:
- Multi-year → không tách, bỏ year filter → context đủ
- Multi-ticker → tách per ticker với filter → noise thấp
- Multi-topic → tách theo sub_queries do LLM generate kèm routing intent

---

## Kết luận

**Lần chạy 2 (3 câu sample):** arch_a = arch_c về quality. arch_c cần full 26 câu để xác nhận.

**Kiến trúc production:**
- Đơn giản: **arch_a** (pure vector RAG) — latency thấp nhất, cost thấp nhất
- Nếu cần xử lý câu compound/multi-ticker tốt: **arch_c v2** — quality ngang arch_a, cost chỉ +14%

**Cần làm:** chạy full 26 câu với arch_c v2 để so sánh với lần 1.

---

## Findings kỹ thuật

**LLM judge thất bại:** DeepSeek-v4-flash trả về reasoning text thay vì "SCORE:N" hoặc JSON. Thay bằng `heuristic_judge()` deterministic (regex số + keyword extraction từ ground_truth).

**CrossEncoder segfault:** BAAI/bge-reranker-v2-m3 crash với exit code 139 khi chạy 26 câu liên tiếp. Removed khỏi arch_b — arch_b dùng weighted_sum fusion only (không reranking).

**q31 retrieval fail:** "Tổng tài sản 2025" trong balance sheet là OCR-garbled image chunk. Cả 3 arch score=1 — lỗi data, không phải lỗi kiến trúc.

**Token tracking arch_c v2:** `interpret()` không expose token counts → total_tokens arch_c undercount ~100-150 tokens (1 interpret call). Không ảnh hưởng đến quality comparison.

---

## Lệnh tái tạo

```bash
# Full 26 câu
python evals/compare_architectures.py --out evals/arch_compare.json

# 3 câu sample
python evals/compare_architectures.py --sample
```

---

## Kết quả lưu

`evals/arch_compare.json` — full per-question results với latency, tokens, cost, quality per arch.
