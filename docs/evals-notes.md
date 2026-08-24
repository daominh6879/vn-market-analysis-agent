# Evals — Notes

## Mục đích tổng thể

Đo chất lượng RAG pipeline qua 2 giai đoạn:

```
Baseline (hiện tại)          Sau khi có RAG
────────────────────         ──────────────────────
Model tự bịa số liệu    →    Model đọc từ tài liệu thật
faithfulness thấp       →    faithfulness cao
context_recall ≈ 0      →    context_recall cao
```

---

## `golden_hpg.yaml` — Tập câu hỏi chuẩn

25 câu hỏi về BCTC Hòa Phát (HPG), manually curated. Mỗi câu có `question`, `answer` (ground truth), và nguồn tài liệu.

| Group | Số câu | Mô tả |
|---|---|---|
| `table_lookup` | 8 | Số trong bảng tài chính |
| `text_interpretation` | 5 | Thông tin trong văn xuôi thuyết minh |
| `multi_period` | 4 | Ghép số liệu nhiều kỳ |
| `multi_source` | 3 | Kết hợp ≥2 tài liệu |
| `no_answer` | 3 | Không có trong tài liệu → model **phải từ chối** |
| `out_of_scope` | 2 | Ngoài domain tài chính → model **phải từ chối** |

- Nhóm 1–4 → chấm bằng RAGAS
- Nhóm 5–6 → chấm pass/fail (có từ chối đúng không)

---

## Hai LLM, hai vai trò

```
câu hỏi tài chính
        │
        ▼
┌─────────────────────┐
│  LLM chính          │  Anthropic / OpenAI (theo LLM_PROVIDER env)
│  ask_baseline()     │  Trả lời câu hỏi HPG — KHÔNG có tài liệu
└─────────────────────┘
        │ answer (hiện tại: bịa vì không có RAG)
        ▼
┌─────────────────────┐
│  Ollama (llama3)    │  Local, miễn phí
│  compute_ragas()    │  Chấm điểm answer — không trả lời câu hỏi tài chính
└─────────────────────┘
```

---

## Ollama làm gì

Không trả lời câu hỏi tài chính. Chỉ làm **judge** — RAGAS truyền vào:

```
question     = câu hỏi gốc
answer       = câu trả lời của LLM chính
contexts     = chunk retrieval (hoặc placeholder nếu chưa có RAG)
ground_truth = đáp án đúng từ golden_hpg.yaml
```

Ollama phán xét theo 4 metrics:

| Metric | Câu hỏi judge | Ý nghĩa |
|---|---|---|
| Faithfulness | Answer có nói gì ngoài contexts không? | Model có bịa không |
| ResponseRelevancy | Answer có trả lời đúng question không? | Có lạc đề không |
| ContextPrecision | Contexts xếp hạng cao có liên quan ground_truth không? | Retrieval có chính xác không |
| ContextRecall | ground_truth có thể suy ra từ contexts không? | Retrieval có đủ không |

---

## Vấn đề hiện tại (chưa có RAG)

`ask_baseline()` gọi LLM **không có context** — model phải tự nhớ số liệu:

```python
return resp.text, []  # contexts luôn rỗng
```

Số liệu như `"43.515,59 tỷ đồng"` (hàng tồn kho Q1-2026) không có trong training data → **model bịa**. Đây là kết quả mong đợi của baseline.

RAGAS nhận `contexts = ["[no context — baseline mode]"]` (placeholder) → faithfulness và context_recall sẽ thấp — đúng như mong đợi.

---

## Regression check

```
baseline.json (lưu lần đầu)
        │
        ▼
mỗi lần chạy tiếp theo → so sánh
        │
        ├── metric drop > 5% → CI fail (sys.exit(1))
        └── metric ok        → ✅ pass
```

---

## Cách dùng

```bash
# Lần đầu: lưu baseline
python evals/run.py --save-baseline

# CI: so sánh vs baseline
python evals/run.py

# Nhanh: chỉ check refusal, không cần Ollama
python evals/run.py --only-refusal --skip-ragas

# Đổi model judge
python evals/run.py --ollama-model mistral
```

---

## Ragas import fix

`ragas 0.4.x` hard-import `ChatVertexAI` từ `langchain_community` đã bị xóa. Fix: inject module thật từ `langchain-google-vertexai` vào `sys.modules` trước khi ragas load. Project không dùng VertexAI — fix chỉ để ragas import thành công.
