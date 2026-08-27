# Bài 26 — Daily Brief Phase 2: Khối ngoại + Hiệu suất nhóm ngành

## Kết quả

- **39 tests xanh** (`tests/test_phase2.py`)
- Tool `get_foreign_flows` DB-first (foreign_flows table) với fallback VCI live
- Tool `get_sector_performance` DB-first (JOIN ohlcv_daily × securities) với fallback hose_universe seed
- Ingest script `ingest/fetch_foreign_flows.py` — fetch VCI price board → upsert Postgres

## Artifacts

| File | Vai trò |
|---|---|
| `tools/foreign_flow_db.py` | Query layer: query_latest_foreign_date, query_market_foreign_net, query_top_foreign, upsert_foreign_rows |
| `tools/price.py` (Tool 7-8) | get_foreign_flows, get_sector_performance, _build_foreign_result, _get_foreign_flows_live |
| `ingest/fetch_foreign_flows.py` | Fetch VCI price board endpoint theo từng batch 50 mã → upsert foreign_flows |
| `infra/migrations/009_foreign_flows.sql` | Bảng foreign_flows (ticker, date, buy_value, sell_value, net_value, buy_volume, sell_volume, net_volume) |
| `tests/test_phase2.py` | 39 tests: DB layer, tool logic, ingest script, registry |

## Schema bảng foreign_flows

```sql
CREATE TABLE foreign_flows (
    ticker       TEXT    NOT NULL,
    date         DATE    NOT NULL,
    buy_value    BIGINT,
    sell_value   BIGINT,
    net_value    BIGINT,
    buy_volume   BIGINT,
    sell_volume  BIGINT,
    net_volume   BIGINT,
    PRIMARY KEY (ticker, date)
);
```

## Luồng dữ liệu

```
get_foreign_flows(days=1)
  └─ query_latest_foreign_date()   → date from foreign_flows
  └─ query_market_foreign_net()    → {total_buy, total_sell, net_value}
  └─ query_top_foreign(n=5, buy)   → top buyers list
  └─ query_top_foreign(n=5, sell)  → top sellers list
  └─ _build_foreign_result()       → ToolResult ok / no_data
       ↑
       fallback nếu DB rỗng: _get_foreign_flows_live() → VCI price board
```

```
get_sector_performance(period="day")
  └─ _query_sector_performance_db()        → JOIN ohlcv_daily × securities
  └─ (fallback) _query_sector_performance_fallback()  → JOIN với hose_universe seed
  └─ ToolResult ok / no_data
```

## Quyết định kỹ thuật

- **Lazy import pattern**: `foreign_flow_db.py` dùng `from core.db import get_conn` bên trong từng hàm (không import module-level). Patch target trong test phải là `core.db.get_conn`, không phải `tools.foreign_flow_db.get_conn`. Tham khảo `test_phase5.py` dùng `data.db.get_conn`.
- **_build_foreign_result**: tách riêng để test trực tiếp logic format message mà không cần DB mock.
- **Batch 50**: VCI price board endpoint có giới hạn payload — chunk 50 mã/request.

## Patch targets

| Hàm cần test | Patch target |
|---|---|
| `foreign_flow_db.query_*` DB calls | `core.db.get_conn` |
| `get_foreign_flows` → lazy imports | `tools.foreign_flow_db.query_latest_foreign_date` v.v. |
| `get_sector_performance` DB path | `tools.price._query_sector_performance_db` |
| `fetch_and_upsert` → upsert | `tools.foreign_flow_db.upsert_foreign_rows` |

## Lệnh chạy

```bash
# Ingest foreign flows hôm nay:
python ingest/fetch_foreign_flows.py

# Ingest theo ngày:
python ingest/fetch_foreign_flows.py --date 2026-08-25

# Chạy tests Phase 2:
python -m pytest tests/test_phase2.py -v
```
