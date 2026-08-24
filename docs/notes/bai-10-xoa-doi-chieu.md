# Bài 10 — Xoá tài liệu & đối chiếu (2026-08-20)

**Vấn đề:** Không có cơ chế xoá đúng → hệ thống có thể trả lời từ tài liệu đã thu hồi.

## Files mới

| File | Mục đích |
|------|----------|
| `infra/migrations/001_documents.sql` | Tạo bảng `documents` |
| `data/db.py` | Helper kết nối Postgres |
| `data/delete.py` | `soft_delete(doc_id, collection)` |
| `data/reconcile.py` | `reconcile(collection, fix=False)` |

**`rag/index.py`:** thêm `_register_doc()` — upsert vào bảng `documents` sau mỗi lần index.

## Cơ chế soft_delete (2 bước atomic)

1. `UPDATE documents SET status='deleted', deleted_at=NOW()` — kiểm toán
2. `qdrant.delete(filter doc_id)` — không trả lời từ doc đã thu hồi
Nếu bước 2 fail → Postgres rollback → không lệch.

## Cơ chế reconcile

- `pg_active = SELECT doc_id WHERE status='active' AND collection=?`
- `qdrant_ids = scroll all vectors, lấy payload.doc_id`
- `orphan = qdrant_ids - pg_active` → rác trong Qdrant
- `missing = pg_active - qdrant_ids` → active nhưng không có chunk
- `--fix`: tự xoá orphan

## Commands

```bash
# 1. Chạy migration (1 lần)
python -c "from data.db import run_migration; run_migration('infra/migrations/001_documents.sql')"

# 2. Index lại để đăng ký doc vào Postgres
python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_structural --strategy structural

# 3. Xoá 1 tài liệu
python data/delete.py --doc-id <doc_id> --collection hpg_structural

# 4. Đối chiếu
python data/reconcile.py --collection hpg_structural
python data/reconcile.py --collection hpg_structural --fix
```

## Kết quả verify

- Index 2025 PDF → `active=1, qdrant=1, orphan=0, missing=0` ✓
- `soft_delete` → `active=0, qdrant=0` ✓
- Xoá hết chunk thủ công qua Qdrant client → `missing_in_qdrant=1` ✓

**Note:** Reconcile theo dõi ở **doc_id level** (không phải chunk level). Xoá vài vector nhưng còn chunk nào có `payload.doc_id` → scroll vẫn thấy doc → không báo missing. Phải xoá hết chunk của doc_id mới phát hiện được lệch.
