# RUNBOOK — Đổi Embedding Model

Thực hiện khi cần thay `bge-m3` bằng model khác (ví dụ `nomic-embed-text`).
**Không đổi model rồi index thêm vào collection cũ** — vector space khác nhau, cosine similarity vô nghĩa.

---

## Tại sao phải index lại toàn bộ?

Mỗi embedding model tạo ra không gian vector riêng.
Vector cũ (bge-m3) và vector mới (model X) không thể cùng một collection:
câu hỏi embed bằng model X sẽ không tìm được chunk embed bằng bge-m3.

---

## Các bước

### 1. Tạo collection mới

```bash
# Đặt tên theo convention: hpg_<strategy>_<model_short>
# Ví dụ: hpg_structural_nomic
export NEW_COLLECTION=hpg_structural_nomic
export NEW_MODEL=nomic-embed-text
```

Pull model về Ollama trước:
```bash
ollama pull $NEW_MODEL
```

### 2. Full rebuild vào collection mới

Chạy từ Dagster UI: **ingestion_full_rebuild_job**
Hoặc CLI:

```bash
python rag/index.py \
  --input outputs/hpg_pymupdf.md \
  --collection $NEW_COLLECTION \
  --strategy structural \
  --embed $NEW_MODEL
```

Kiểm tra số chunk:
```bash
# Mở http://localhost:6333/dashboard → Collections → $NEW_COLLECTION
# Count phải ≈ số chunk của collection cũ
```

### 3. Kiểm tra eval

```bash
QDRANT_COLLECTION=$NEW_COLLECTION python evals/run.py \
  --questions evals/golden_hpg.yaml --skip-ragas
```

Yêu cầu: **faithfulness ≥ baseline**. Nếu thấp hơn → không đổi.

### 4. Đổi alias (không downtime)

```bash
# Qdrant alias API
curl -X POST http://localhost:6333/collections/aliases \
  -H 'Content-Type: application/json' \
  -d '{
    "actions": [{
      "create_alias": {
        "alias_name": "hpg_structural",
        "collection_name": "'"$NEW_COLLECTION"'"
      }
    }]
  }'
```

> Sau bước này query dùng alias `hpg_structural` sẽ đọc từ collection mới.

Cập nhật `.env`:
```
EMBED_MODEL=nomic-embed-text
QDRANT_COLLECTION=hpg_structural
```

### 5. Smoke test

```bash
python evals/run.py --questions evals/golden_hpg.yaml --skip-ragas
# Kết quả phải ≥ bước 3
```

### 6. Xóa collection cũ sau 24h

> Đợi 24h để đảm bảo không có query nào còn dùng collection cũ.

```bash
curl -X DELETE http://localhost:6333/collections/hpg_structural_bge_m3
```

---

## Rollback

Nếu eval thất bại ở bước 3 hoặc 5:
1. Đổi alias về collection cũ: thay `collection_name` = tên collection cũ.
2. Revert `.env`.
3. Xóa collection mới.

---

## Checklist

- [ ] Pull model mới về Ollama
- [ ] Tạo collection mới, full rebuild hoàn thành
- [ ] Eval ≥ baseline
- [ ] Đổi alias thành công
- [ ] Smoke test sau khi đổi alias
- [ ] Xóa collection cũ sau 24h
- [ ] Cập nhật `EMBED_MODEL` trong `.env` và `docs/notes/`

---

## Lần chạy thực tế

> Ghi lại kết quả lần chạy đầu tiên ở đây.

| Ngày | Model cũ | Model mới | Collection mới | Eval cũ | Eval mới | Kết quả |
|------|----------|-----------|---------------|---------|---------|---------|
| _chưa chạy_ | | | | | | |
