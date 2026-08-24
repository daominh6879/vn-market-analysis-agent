# Bài 4 — Eval Baseline (2026-08-09)

**Config:** model trần `qwen3:8b` (không RAG), RAGAS judge `qwen3:8b` local

| Metric | Score |
|---|---|
| refusal_pass_rate | 0.800 |

**Nhận xét:**
- table_lookup, text_interpretation, multi_period, multi_source: model trả lời "không có" — đúng (không có context)
- q25 (out_of_scope): fail — model cố trả lời thay vì từ chối

**Fix RAGAS timeout:** `RunConfig(timeout=600, max_workers=2)` trong `evals/run.py`  
Lý do: local Ollama mỗi call ~40-60s, default `max_workers=16` → timeout. Cloud API cần để chạy RAGAS đầy đủ.

**Commands:**
```bash
python evals/run.py --skip-ragas          # nhanh, chỉ refusal
python evals/run.py --save-baseline       # lưu baseline
```
