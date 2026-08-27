# Bài 23 · OHLCV DB — Lưu thị trường vào Postgres, query thay API

## Kết quả

- **330 rows** upserted: 11 tickers × 30 ngày (2026-07-16 → 2026-08-26).
- `get_market_performance` và `get_market_breadth` query Postgres, không gọi VCI live mỗi lần.
- 94 existing tests pass, 0 regression.
- 2 MCP tools mới: `get_market_perf`, `get_breadth`.

## Thay đổi

| File | Thay đổi |
|------|----------|
| `infra/migrations/006_ohlcv_daily.sql` | Mới. Bảng `ohlcv_daily(ticker, date, open, high, low, close, volume)` |
| `ingest/fetch_ohlcv.py` | Mới. Fetch VCI → upsert `ohlcv_daily`. CLI: `--tickers VN30,HPG --days 30` |
| `pipeline/assets_ohlcv.py` | Mới. Dagster asset `ohlcv_daily_ingest`, schedule 18:30 weekdays |
| `pipeline/assets.py` | Thêm `ohlcv_daily_ingest` vào `Definitions` |
| `tools/ohlcv_db.py` | Mới. Query layer: `query_ohlcv()`, `query_vn30_latest()` |
| `tools/price.py` | `get_market_performance` + `get_market_breadth`: DB-first, VCI fallback |
| `tools/providers.py` | Thêm `fetch_batch_latest()` — 1 API call cho nhiều tickers |
| `tools/mcp_server.py` | Thêm `get_market_perf`, `get_breadth` |
| `tools/registry.py` | Thêm metadata cho 2 tools mới |

## Lý do chuyển sang DB-first

**Trước:** mỗi query `get_market_performance("year")` → fetch 250+ phiên từ VCI → chậm, tốn rate limit.

**Sau:** Dagster fetch 1 lần/ngày lúc 18:30 → upsert DB → tool query SQL <100ms.

| | Live-fetch | DB-first |
|--|--|--|
| Latency | 2–5s / call | <100ms |
| Rate limit | Risk cao | Fetch 1 lần/ngày |
| History depth | Bị giới hạn bởi VCI response | Giữ vĩnh viễn |
| VN30 breadth | 30 concurrent calls | 1 SQL JOIN |

## Schema

```sql
CREATE TABLE ohlcv_daily (
    ticker      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      BIGINT  NOT NULL DEFAULT 0,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);
```

## Pattern DB-first với fallback

```python
# Dùng trong get_market_performance và get_market_breadth
from tools.ohlcv_db import query_ohlcv
df = query_ohlcv(resolved, days + 15)
if df is not None and len(df) >= 2:
    return _compute_performance_from_df(df, period_key, t)
# Fallback: live VCI API
df = provider.get_history(resolved, days + 15)
```

## Bug đã sửa

**psycopg2 trả `Decimal`, pandas không tự cast** — `query_vn30_latest` lỗi khi tính `pct_change`.

Fix: cast `close` và `prev_close` sang `float` trước khi tính.

**`from __future__ import annotations` phá Dagster Config** — Dagster dùng Pydantic v1-style annotation evaluation tại runtime. Lazy string annotations làm `OhlcvIngestConfig` không resolve được.

Fix: bỏ `from __future__ import annotations` trong `assets_ohlcv.py`.

## Kết quả verify thực tế (2026-08-26)

```
VNINDEX hôm nay:  tăng nhẹ (+1.75%)   close: 1,970
VNINDEX tuần này: tăng mạnh (+5.03%)
VNINDEX tháng này: tăng mạnh (+9.05%)

VN30 breadth: 9 tăng / 1 đứng / 0 giảm
Top tăng: TCB +6.9% | VIC +4.3% | FPT +2.7% | MBB +1.9% | VCB +1.5%
```

## Lệnh chạy lại

```bash
# Chạy migration (1 lần)
python -c "from core.db import run_migration; run_migration('infra/migrations/006_ohlcv_daily.sql')"

# Backfill lịch sử đầy đủ (365 ngày)
python ingest/fetch_ohlcv.py --tickers VN30,ACB,BCM,BID,BVH,CTG,FPT,GAS,GVR,HDB,HPG,MBB,MSN,MWG,PDR,PLX,POW,SAB,SHB,SSB,SSI,STB,TCB,TPB,VCB,VHM,VIB,VIC,VJC,VNM,VPB --days 365

# Backfill ngắn (daily incremental)
python ingest/fetch_ohlcv.py --tickers VN30 --days 5

# Test tools
python -m pytest tests/test_tools.py tests/test_tool_chaos.py -q
```

## Còn thiếu

- `get_market_breadth` chỉ có volume=0 khi từ DB (VCI batch không trả volume riêng). Cần fetch `volume` qua `ohlcv_daily` đầy đủ sau khi backfill.
- "Quý sau" = forward-looking — cần `get_macro_calendar` (NHNN schedule, earnings). Chưa có data source.
