# BCTC Reset & Ingestion Guide

Hướng dẫn reset toàn bộ data BCTC và ingest PDF mới qua Dagster pipeline.

---

## 1. Reset data

```bash
# Dry-run — xem plan, không thay đổi gì
python scripts/reset_bctc.py --dry-run

# Thực thi reset
python scripts/reset_bctc.py

# Reset chỉ Qdrant + Postgres, giữ MinIO
python scripts/reset_bctc.py --skip-minio
```

Những gì bị xóa:

| Hệ thống | Target |
|----------|--------|
| MinIO | Tất cả bucket `*-docs` (hpg-docs, vcb-docs, ...) |
| Qdrant | `bctc_structural` + legacy `hpg_structural`, `hpg_b7_*` |
| Postgres | `documents`, `financial_facts`, `quarantine_log` (TRUNCATE) |

---

## 2. Upload PDF lên MinIO

### Bucket naming

Mỗi ticker có bucket riêng, Dagster tự tạo nếu chưa có:

```
hpg-docs/
vcb-docs/
fpt-docs/
...
```

### Object key convention

Dagster scan bucket **đệ quy**, không bắt buộc cấu trúc folder. Khuyến nghị:

```
{year}/{report_type}/{filename}.pdf
```

Ví dụ:

```
hpg-docs/
  2024/standalone/hpg_bctc_2024.pdf
  2025/standalone/hpg_bctc_2025.pdf

vcb-docs/
  2024/consolidated/vcb_bctc_2024.pdf
  2024/standalone/vcb_bctc_standalone_2024.pdf
```

`report_type` hợp lệ: `standalone` (công ty mẹ) | `consolidated` (hợp nhất)

### Upload qua mc (MinIO CLI)

```bash
mc cp hpg_bctc_2024.pdf myminio/hpg-docs/2024/standalone/hpg_bctc_2024.pdf
```

Hoặc dùng MinIO UI tại `http://localhost:9001`.

---

## 3. Chạy Dagster pipeline

### Cấu hình tickers cho sensor

Trong `.env` hoặc docker-compose:

```env
TICKERS=HPG,VCB
```

Sensor `minio_new_pdf_sensor` monitor các bucket tương ứng.

### Trigger thủ công (Dagster UI)

Vào **Jobs → ingestion_job → Launchpad**, config:

```json
{
  "ops": {
    "raw_pdf": {
      "config": {
        "ticker": "HPG",
        "period": "2024",
        "report_type": "standalone",
        "mode": "incremental"
      }
    }
  }
}
```

`mode`:
- `incremental` — chỉ index file chưa có trong Postgres `documents`
- `full_rebuild` — index lại tất cả dù đã indexed

### Payload lưu vào Qdrant

Mỗi chunk trong `bctc_structural` có payload:

```json
{
  "ticker": "HPG",
  "year": "2024",
  "report_type": "standalone",
  "source_key": "2024/standalone/hpg_bctc_2024.pdf",
  "dagster_run_id": "...",
  "text": "..."
}
```

> **Lưu ý:** Field `sector` chưa có trong payload — chưa implement.

---

## 4. Verify sau ingestion

```bash
# Đối chiếu Postgres vs Qdrant
python core/reconcile.py --collection bctc_structural

# Fix orphan nếu có
python core/reconcile.py --collection bctc_structural --fix
```

### Kiểm tra nhanh qua Python

```python
from qdrant_client import QdrantClient
client = QdrantClient("localhost", port=6333)
info = client.get_collection("bctc_structural")
print(info.points_count)  # tổng số chunks

# Đếm theo ticker
from qdrant_client.models import FieldCondition, Filter, MatchValue
res = client.count("bctc_structural",
    count_filter=Filter(must=[FieldCondition(key="ticker", match=MatchValue(value="HPG"))]))
print(res.count)
```

---

## 5. Test RAG sau indexing

```bash
# Demo query HPG
python rag/demo_rag_fusion.py --collection bctc_structural --ticker HPG

# Query tùy chỉnh
python rag/demo_rag_fusion.py --query "Doanh thu HPG 2024 là bao nhiêu?" --ticker HPG
```

---

## Sơ đồ flow

```
PDF files
   │
   ▼
MinIO bucket ({ticker}-docs)
   │
   ▼  [Dagster sensor / manual trigger]
raw_pdf asset  ──── scan bucket, filter unindexed
   │
   ▼
parsed_doc asset  ── pymupdf parse → markdown
   │
   ├──► embeddings asset  ──► bctc_structural (Qdrant)
   │                           payload: ticker, year, report_type
   │
   └──► financial_facts asset ─► financial_facts (Postgres)
```
