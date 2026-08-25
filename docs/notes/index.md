# NOTES — Kết quả & Quyết định

Ghi chép thực nghiệm, số đo, quyết định kỹ thuật. Không giải thích khái niệm — xem `EXPLAIN.md`.

> File này là index. Nội dung chi tiết ở `docs/notes/`.

---

## Progress tracker

- [x] Bài 4 — Eval baseline (refusal_pass_rate=0.80)
- [x] Bài 5 — Noise floor (std=0.0894, ngưỡng CI=0.1789)
- [x] Bài 6 — PDF parse comparison (pymupdf4llm + vie+eng)
- [x] Bài 7 — Chunking strategies (structural thắng, avg RAGAS 0.640)
- [x] Bài 8 — Embedding model selection (bge-m3 thắng, avg RAGAS 0.376)
- [x] Bài 9 — Idempotent index (uuid5 chunk IDs, delete-before-upsert)
- [x] Bài 10 — Xoá tài liệu & đối chiếu (soft_delete + reconcile verified)
- [x] Bài 11 — Cửa lọc chất lượng (in progress)
- [x] Bài 12 — Financial facts + SQL (postgres financial_facts)
- [x] Bài 13 — Pipeline Dagster (assets.py)
- [x] Bài 14 — BM25 + tách từ tiếng Việt (refusal 0.80→1.00, 6 ví dụ)
- [x] Bài 15 — So sánh 6 collections, chọn structural_meta (recall 0.524, MAP 0.255)
- [x] Bài 15 (fusion) — weighted_sum thắng hit@5 13/21 (+2 vs vector); candidate_k=30 giải quyết regression @20
- [x] Bài 16 — Reranker CrossEncoder: fusion_ws vẫn thắng (13/21); reranker 11/21 + 20s p95 — không deploy

## Pipeline hiện tại

**structural + bge-m3 + metadata (`hpg_b7_structural_meta`)**

## Per-lesson notes

| Bài | File | Tóm tắt |
|-----|------|---------|
| Setup | [setup.md](setup.md) | Docker bind mounts, quy tắc làm việc |
| eval | [collection-eval.md](collection-eval.md) | 6 collections × 3 retrievers; structural_meta wins (recall 0.524) |
| Bài 4 | [bai-4-eval-baseline.md](bai-4-eval-baseline.md) | refusal_pass_rate=0.800 |
| Bài 5 | [bai-5-noise-floor.md](bai-5-noise-floor.md) | std=0.0894, ngưỡng CI=0.1789 |
| Bài 6 | [bai-6-pdf-parse.md](bai-6-pdf-parse.md) | pymupdf4llm + vie+eng wins |
| Bài 7 | [bai-7-chunking.md](bai-7-chunking.md) | structural no-meta wins (avg 0.640) |
| Bài 8 | [bai-8-embedding.md](bai-8-embedding.md) | bge-m3 wins (avg 0.376, dims=1024) |
| Bài 9 | [bai-9-idempotent.md](bai-9-idempotent.md) | uuid5 chunk IDs, delete-before-upsert |
| Bài 10 | [bai-10-xoa-doi-chieu.md](bai-10-xoa-doi-chieu.md) | soft_delete + reconcile verified |
| Bài 11 | [bai-11-chat-luong.md](bai-11-chat-luong.md) | quality filter in progress |
| Bài 12 | [bai-12-facts-sql.md](bai-12-facts-sql.md) | financial_facts postgres, fetch prices |
| Bài 13 | [bai-13-pipeline-dagster.md](bai-13-pipeline-dagster.md) | Dagster assets pipeline |
| Bài 14 | [bai-14-bm25.md](bai-14-bm25.md) | BM25 raw 0.80 → VN tokenize 1.00 refusal; 6 ví dụ BM25 vs vector |
| Bài 15 | [bai-15-fusion.md](bai-15-fusion.md) | weighted_sum chọn (candidate_k=30): hit@5 13/21 (+2 vs vector), fusion@20 = vector@20 = 17/21 |
| Bài 16 | [bai-16-reranker.md](bai-16-reranker.md) | CrossEncoder thất bại: 11/21 hit@5 + p95=20s vs fusion 13/21 + 5s; structural table chunks không phù hợp cross-encoder |
