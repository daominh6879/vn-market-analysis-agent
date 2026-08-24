# Bài 9 — Idempotent index pipeline (2026-08-19)

**Vấn đề:** `rag/index.py` dùng `recreate_collection` → chạy lại = drop toàn bộ collection → không idempotent.

## Thay đổi

| File | Thay đổi |
|------|----------|
| `data/contracts.py` | Mới — Pydantic `ParsedDoc` + `compute_doc_id(bytes)` |
| `rag/index.py` | `recreate_collection` → `ensure_collection` + `delete_doc_chunks`; chunk ID = `UUID5(ns, "{doc_id}_{i:04d}")` |
| `tests/test_idempotent.py` | Mới — test chạy pipeline 2 lần, assert count + IDs bằng nhau |

## Cơ chế

- `doc_id = sha256(content)[:16]` — xác định từ nội dung file
- Trước upsert: `delete_doc_chunks(collection, doc_id)` xoá chunk cũ của đúng doc này
- Chunk point ID = `UUID5(namespace, f"{doc_id}_{i:04d}")` — deterministic, không đổi giữa các lần chạy
- Collection chỉ tạo nếu chưa có; nhiều doc cùng chung 1 collection được

## Commands

```bash
# Test schema (không cần services):
uv run pytest tests/test_idempotent.py::test_changed_content_changes_doc_id tests/test_idempotent.py::test_empty_content_rejected -v

# Test idempotent thật (cần Qdrant + Ollama bge-m3):
uv run pytest tests/test_idempotent.py::test_idempotent -v -s

# Index như cũ (giờ idempotent):
python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_fixed_512 --strategy fixed
```

**Xong khi:** `test_idempotent` xanh — chạy lần 2 count và IDs không đổi.
