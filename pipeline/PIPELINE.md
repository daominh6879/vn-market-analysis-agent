# Pipeline — Ingestion HPG PDF

## Tổng quan

Biến các script rời (bài 6–12) thành pipeline tự chạy dùng Dagster.
PDF mới thả vào MinIO → 5 phút sau truy vấn được, không cần chạm gì.

## Khởi động

```bash
# Cài dependencies
uv add dagster dagster-webserver dagster-postgres dagster-aws minio

# Chạy Dagster UI
dagster dev -f pipeline/assets.py
# Mở http://localhost:3000
```

## Flow

```
MinIO bucket (hpg-docs)
        │
        ▼
    raw_pdf
    Quét PDF, lọc file chưa index
        │
        ▼
  parsed_doc
  Download PDF → pymupdf4llm → markdown → quality gate
        │
   ┌────┴────┐
   ▼         ▼
embeddings  financial_facts
(Qdrant)    (Postgres)
```

## Assets

| Asset | Retry | Mô tả |
|-------|-------|-------|
| `raw_pdf` | — | Quét MinIO, lọc incremental |
| `parsed_doc` | 2×15s | Parse PDF, quality gate |
| `embeddings` | 3×30s | Chunk → embed → Qdrant upsert |
| `financial_facts` | 2×20s | LLM extract → validate → Postgres |

`embeddings` và `financial_facts` nhận cùng output `parsed_doc` → chạy song song, parse PDF chỉ 1 lần.

## Hai chế độ chạy

| Mode | Khi nào dùng |
|------|-------------|
| `incremental` | Mặc định — chỉ xử lý file chưa có trong Postgres `documents` |
| `full_rebuild` | Đổi embedding model, schema thay đổi, index lại toàn bộ |

## Tự động hóa

**Sensor** `minio_new_pdf_sensor`
- Poll mỗi 5 phút
- Cursor = sorted list PDF key trong MinIO
- Phát hiện key mới → kick off `ingestion_job` per file

**Schedule** `daily_ingestion_0600`
- Cron `0 6 * * *`
- Chạy incremental mỗi sáng 6:00

## Audit trail

Mỗi chunk trong Qdrant có payload:
```json
{
  "doc_id": "abc123def456",
  "dagster_run_id": "550e8400-...",
  "source_key": "2024/hpg_q4_bctc.pdf"
}
```

Từ Dagster UI → **Asset catalog → embeddings → Last run** → thấy file gốc và run nào tạo ra.

## Cảnh bẫy

**Đừng embed song song** — Ollama embedding API bị quá tải.
`embeddings` asset xử lý tuần tự từng doc. Không dùng `ThreadPoolExecutor` hay `DynamicOutput` ở bước này.

## Đổi embedding model

Xem `../data/RUNBOOK.md`.
Tóm tắt: tạo collection mới → full_rebuild → eval → đổi alias → xóa cũ sau 24h.

## Biến môi trường

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `TICKERS` | `HPG` | Danh sách ticker sensor monitor, ví dụ `HPG,VCB,MWG` |
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO URL |
| `MINIO_ROOT_USER` | `minioadmin` | Access key |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | Secret key |
| `EMBED_MODEL` | `bge-m3` | Ollama embedding model |
| `CHUNK_STRATEGY` | `structural` | `fixed` / `structural` / `hierarchical` |

Bucket và collection **tự sinh từ ticker** — không cần cấu hình tay:
```
ticker=HPG  →  bucket=hpg-docs      collection=hpg_structural
ticker=VCB  →  bucket=vcb-docs      collection=vcb_structural
ticker=MWG  →  bucket=mwg-docs      collection=mwg_structural
```

## Checklist hoàn thành

- [ ] `dagster dev -f pipeline/assets.py` khởi động không lỗi
- [ ] Materialize `raw_pdf` từ UI, xem log
- [ ] Thả 3 PDF mới vào MinIO → sensor phát hiện → job tự chạy
- [ ] Truy vấn được sau ≤ 5 phút, không chạm gì
- [ ] Click chunk trong Qdrant → thấy `source_key` + `dagster_run_id`
- [ ] Chạy runbook đổi embedding model 1 lần (ghi kết quả vào `../data/RUNBOOK.md`)
