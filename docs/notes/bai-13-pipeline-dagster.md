# Bài 13 — Biến script thành pipeline tự chạy (Dagster)

## Files tạo mới

| File | Mục đích |
|------|----------|
| `pipeline/assets.py` | Dagster assets + sensor + schedule + jobs |
| `data/RUNBOOK.md` | Vận hành khi đổi embedding model |

## Kiến trúc pipeline

```
raw_pdf (MinIO scan)
    ↓
parsed_doc (parse_with_pymupdf + quality gate)
    ↓               ↓
embeddings     financial_facts
(Qdrant)        (Postgres)
```

Hai nhánh `embeddings` và `financial_facts` chạy song song từ `parsed_doc`.

## Assets

| Asset | Retry | Mô tả |
|-------|-------|-------|
| `raw_pdf` | — | Quét MinIO, lọc file chưa index (incremental) |
| `parsed_doc` | 2×15s | Parse PDF, quality gate |
| `embeddings` | 3×30s | Chunk → embed → Qdrant upsert |
| `financial_facts` | 2×20s | LLM extract → validate → Postgres insert |

## Sensor và Schedule

- **minio_new_pdf_sensor**: poll mỗi 5 phút, phát hiện PDF mới qua cursor = sorted key list
- **daily_ingestion_0600**: cron `0 6 * * *`, chạy incremental mỗi ngày 6:00

## Hai chế độ chạy

| Mode | Hành vi |
|------|---------|
| `incremental` | Chỉ index file chưa có doc_id active trong Postgres `documents` |
| `full_rebuild` | Index tất cả file (dùng khi đổi embedding model) |

## Cài Dagster

```bash
uv add dagster dagster-webserver dagster-postgres dagster-aws minio
```

## Chạy

```bash
make pipeline-dev
# Mở http://localhost:3000
# Materialize raw_pdf → xem log
```

## Audit trail

Mỗi chunk trong Qdrant có payload:
```json
{
  "doc_id": "abc123...",
  "dagster_run_id": "uuid-...",
  "source_key": "2024/hpg_q4.pdf"
}
```

Từ Dagster UI: **Asset catalog → embeddings → Last run** → thấy file gốc.
Từ Qdrant: scroll payload → `source_key` = tên file trong MinIO.

## Cảnh bẫy embedding concurrency

`embeddings` asset xử lý tuần tự (một doc mỗi lần) — tránh bắn quá tải vào Ollama.
**Không dùng `ThreadPoolExecutor` hay Dagster `DynamicOutput` ở bước embed.**

## TODO — Checklist hoàn thành

- [ ] `uv add dagster dagster-webserver minio` chạy thành công
- [ ] `dagster dev -f pipeline/assets.py` khởi động, mở được UI http://localhost:3000
- [ ] Materialize `raw_pdf` từ UI, xem log
- [ ] Thả 3 PDF mới vào MinIO → sensor phát hiện → ingestion_job tự chạy
- [ ] Click chunk trong Qdrant, truy ngược về `source_key` + `dagster_run_id`
- [ ] Chạy runbook đổi embedding model ít nhất 1 lần (ghi kết quả vào `data/RUNBOOK.md`)
