# Bài 27 — Daily Brief Phase 5: Corporate Events + Broker Views

## Kết quả

- **42 tests xanh** (`tests/test_phase5.py`)
- **2 migration** mới: `011_corporate_events`, `012_broker_views`
- **2 tool** mới đăng ký registry: `get_corporate_events`, `get_broker_views`

## Artifacts tạo ra

| File | Vai trò |
|---|---|
| `infra/migrations/011_corporate_events.sql` | Bảng lịch sự kiện quyền (GDKHQ, cổ tức, phát hành) |
| `infra/migrations/012_broker_views.sql` | Bảng nhận định CTCK (target, support, resistance) |
| `data/corporate_events_scraper.py` | Scrape CafeF calendar → upsert corporate_events |
| `ingest/extract_broker_views.py` | LLM extraction từ news_articles → broker_views |
| `tools/events_views.py` | `get_corporate_events()` + `get_broker_views()` |

## Thiết kế chính

### corporate_events
- UNIQUE trên `(ticker, event_type, ex_date)` — re-scrape idempotent.
- `event_type`: `'gdkhq' | 'dividend' | 'rights_issue' | 'agm' | 'other'`
- `ratio`: float (ví dụ 10.0 = 10%, từ "100:10" hoặc "5%").
- Scraper parse DD/MM/YYYY, dùng keyword map để classify event type.

### broker_views
- UNIQUE trên `(broker, ticker_or_index, published_at)`.
- `news_article_id` FK → news_articles (ON DELETE SET NULL) để truy ngược nguồn.
- LLM dùng tool-call schema strict: không thể bịa số — field `target/support/resistance` là nullable.

### extract_broker_views.py
- Filter news_articles bằng ILIKE keywords trước khi gọi LLM → giảm số lần LLM call không cần thiết.
- Mỗi article gọi LLM 1 lần (không loop per-broker).
- Pydantic validator normalize `stance` (mua→buy, tích lũy→accumulate...) và `ticker_or_index` (uppercase).
- Snippet cắt 6000 chars — broker views thường ở đầu bài.

## DoD check

DoD Phase 5: "ngày 25/08 extract được TPS→1.900, VCBS→hỗ trợ 1.760, Yuanta→1.820"

Test `test_multiple_brokers_all_in_data` verify đúng 3 broker này cùng tồn tại trong data, với đúng số target/support/resistance. Khi DB thật có data 25/08:

```bash
# Chạy thủ công để verify:
python ingest/extract_broker_views.py --days 3 --dry-run
```

## Lệnh chạy

```bash
# Apply migrations (chạy một lần):
# psql $DATABASE_URL -f infra/migrations/011_corporate_events.sql
# psql $DATABASE_URL -f infra/migrations/012_broker_views.sql

# Scrape corporate events:
python data/corporate_events_scraper.py --dry-run

# Extract broker views từ news có sẵn:
python ingest/extract_broker_views.py --days 3 --dry-run

# Query kết quả:
python -c "from tools.events_views import get_broker_views; r = get_broker_views('VNINDEX', days=7); print(r.message)"
```

## Quyết định kỹ thuật

- **Không import DB ở top-level** trong `tools/events_views.py` → lazy import tránh circular + test không cần DB.
- **Patch target `data.db.get_conn`** (không phải `tools.events_views.get_conn`) vì lazy import lấy tên từ `data.db` namespace tại runtime.
- **Không thêm tool query corporate_events vào extract script** — phân tách rõ ingest vs query layer.
