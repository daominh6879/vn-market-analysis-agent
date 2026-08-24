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
- [ ] Bài 11 — Cửa lọc chất lượng (in progress)

## Pipeline hiện tại

**structural + bge-m3 + không metadata**

## Per-lesson notes

| Bài | File | Tóm tắt |
|-----|------|---------|
| Setup | [setup.md](setup.md) | Docker bind mounts, quy tắc làm việc |
| Bài 4 | [bai-4-eval-baseline.md](bai-4-eval-baseline.md) | refusal_pass_rate=0.800 |
| Bài 5 | [bai-5-noise-floor.md](bai-5-noise-floor.md) | std=0.0894, ngưỡng CI=0.1789 |
| Bài 6 | [bai-6-pdf-parse.md](bai-6-pdf-parse.md) | pymupdf4llm + vie+eng wins |
| Bài 7 | [bai-7-chunking.md](bai-7-chunking.md) | structural no-meta wins (avg 0.640) |
| Bài 8 | [bai-8-embedding.md](bai-8-embedding.md) | bge-m3 wins (avg 0.376, dims=1024) |
| Bài 9 | [bai-9-idempotent.md](bai-9-idempotent.md) | uuid5 chunk IDs, delete-before-upsert |
| Bài 10 | [bai-10-xoa-doi-chieu.md](bai-10-xoa-doi-chieu.md) | soft_delete + reconcile verified |
| Bài 11 | [bai-11-chat-luong.md](bai-11-chat-luong.md) | quality filter in progress |
