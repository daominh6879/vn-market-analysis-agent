# Pipeline — Ingestion HPG PDF + News

## Tổng quan

Biến các script rời (bài 6–12, 12B) thành pipeline tự chạy dùng Dagster.
PDF mới thả vào MinIO → 5 phút sau truy vấn được.
News tự scrape mỗi 6h → embed → sẵn sàng trong `news_chunks`.

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
MinIO bucket (hpg-docs)             CafeF + VnExpress RSS
        │                                    │
        ▼                                    ▼
    raw_pdf                             news_raw
    Quét PDF, lọc file chưa index       Scrape RSS → news_articles (Postgres)
        │                                    │
        ▼                                    ▼
  parsed_doc                          news_indexed
  PDF → markdown → quality gate       Embed → news_chunks (Qdrant)
        │
   ┌────┴────┐
   ▼         ▼
embeddings  financial_facts           news_purge  (weekly)
(Qdrant)    (Postgres)                Delete > 90 ngày
```

Group **ingestion**: `raw_pdf → parsed_doc → embeddings + financial_facts`
Group **news**: `news_raw → news_indexed`, `news_purge` (độc lập)

## Assets

### Group: ingestion

| Asset | Retry | Mô tả |
|-------|-------|-------|
| `raw_pdf` | — | Quét MinIO, lọc incremental |
| `parsed_doc` | 2×15s | Parse PDF, quality gate |
| `embeddings` | 3×30s | Chunk → embed → Qdrant upsert |
| `financial_facts` | 2×20s | LLM extract → validate → Postgres |

`embeddings` và `financial_facts` nhận cùng output `parsed_doc` → chạy song song, parse PDF chỉ 1 lần.

### Group: news

| Asset | Retry | Mô tả |
|-------|-------|-------|
| `news_raw` | 3×60s | Scrape RSS CafeF + VnExpress → upsert `news_articles` |
| `news_indexed` | 3×60s | Embed `indexed_at IS NULL` → upsert `news_chunks` |
| `news_purge` | — | Xóa bài > 90 ngày (Postgres + Qdrant) |

Idempotent: URL là dedup key trong Postgres; `url_hash` là point ID trong Qdrant. Chạy lại không duplicate.

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

**Schedule** `news_6h`
- Cron `0 */6 * * *`
- Scrape + index news mỗi 6 tiếng

**Schedule** `news_purge_weekly`
- Cron `0 2 * * 0`
- Xóa bài cũ > 90 ngày, Chủ nhật 02:00

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
| `EMBED_MODEL` | `bge-m3` | Ollama embedding model (PDF chunks) |
| `CHUNK_STRATEGY` | `structural` | `fixed` / `structural` / `hierarchical` |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embed model cho `news_chunks` (phải cùng dim với `hpg_chunks`) |

Bucket và collection **tự sinh từ ticker** — không cần cấu hình tay:
```
ticker=HPG  →  bucket=hpg-docs      collection=hpg_structural
ticker=VCB  →  bucket=vcb-docs      collection=vcb_structural
ticker=MWG  →  bucket=mwg-docs      collection=mwg_structural
```

## Checklist hoàn thành

**PDF ingestion:**
- [ ] `dagster dev -f pipeline/assets.py` khởi động không lỗi
- [ ] Materialize `raw_pdf` từ UI, xem log
- [ ] Thả 3 PDF mới vào MinIO → sensor phát hiện → job tự chạy
- [ ] Truy vấn được sau ≤ 5 phút, không chạm gì
- [ ] Click chunk trong Qdrant → thấy `source_key` + `dagster_run_id`
- [ ] Chạy runbook đổi embedding model 1 lần (ghi kết quả vào `../data/RUNBOOK.md`)

**News pipeline:**
- [ ] Materialize `news_raw` → Postgres `news_articles` có ≥ 50 bài
- [ ] Materialize `news_indexed` → Qdrant `news_chunks` có điểm, `indexed_at IS NULL` = 0
- [ ] Materialize `news_raw` lần 2 → count không tăng (idempotency)
- [ ] `python rag/news_index.py --search "HPG" --days 30` trả kết quả
- [ ] Pipeline RAG Fusion query "Có tin gì về HPG?" → `sources_used` chứa `TIN TỨC`
