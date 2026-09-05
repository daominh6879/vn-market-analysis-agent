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

---

## Fix 2026-09-05 — khối ngoại ròng luôn = 0

### Triệu chứng
`get_foreign_flows` trả `"Mua ròng 0 tỷ đồng"` cho mọi phiên gần nhất.

### Root cause — 3 bug xếp tầng

**1. Sai đơn vị 1000x lúc ingest.** Fireant chỉ trả foreign *volume*, không có value, nên value được suy ra `volume × close`. Nhưng `priceClose` của Fireant là **nghìn đồng**, không phải VND. Verify từ DB:

```
 ticker |    date    | close | volume
 HPG    | 2026-09-04 |  21.7 | 13920900
 VPB    | 2026-09-04 |  27.8 | 21390000
```

Code chia `/1e9` → phải là `/1e6`. Bằng chứng: `VPB buy_volume 5,185,700 × 27.8 / 1e9 = 0.1442` = đúng giá trị sai nằm trong DB.

Xuất hiện ở **2 chỗ** — `ingest/fetch_foreign_flows.py:102` và `ingest/fetch_ohlcv.py:196`.

**2. Chia `/1e9` lần thứ hai lúc format.** `_build_foreign_result` nhận value đã là tỷ đồng nhưng vẫn `net_bn = net_value / 1e9` → `0.2858/1e9 ≈ 0` → in ra `"0 tỷ"`.

**3. Dagster job đè lên nhau.** Hai asset cùng ghi `foreign_flows`:

| Asset | Schedule | Cron | Nguồn | Value |
|---|---|---|---|---|
| `foreign_flows_ingest` | `foreign_flows_1730` | `30 17 * * 1-5` | VCI price board | thật |
| `ohlcv_daily_ingest` | `ohlcv_ingest_schedule` | `30 18 * * 1-5` | Fireant | suy ra |

Job 18:30 chạy **sau** 1 tiếng, `ON CONFLICT DO UPDATE` ghi đè value thật của VCI bằng value suy ra sai 1000x. Khớp với DB trước fix: `2026-09-04 net = 0.2858` (scale Fireant) vs `2026-08-31 net = 452` (scale VCI, không bị đè).

### Fix

| File | Thay đổi |
|---|---|
| `ingest/fetch_foreign_flows.py:102-107` | `/1e9` → `/1e6` |
| `ingest/fetch_ohlcv.py:196-201` | `/1e9` → `/1e6` |
| `ingest/fetch_ohlcv.py:84-112` | `DO UPDATE` → `DO NOTHING`, trả `cur.rowcount` thật |
| `tools/price.py:1325-1330` | live VCI path: normalize raw VND → tỷ ngay sau fetch |
| `tools/price.py:1351,1364-1365` | bỏ `/1e9` lần 2 trong `_build_foreign_result` |
| `tools/foreign_flow_db.py:52` | sửa docstring đơn vị (ghi sai "raw VND") |

### Data cleanup

```bash
# backfill 365 ngày, đè hết row scale sai
PYTHONIOENCODING=utf-8 python ingest/fetch_foreign_flows.py --migrate   # 36,300 rows

# fetch lại các ngày ngoài window 365d (giữ scale cũ)
python ingest/fetch_foreign_flows.py --tickers "<147 mã>" --date 2025-09-04   # 3,087 rows
```

```sql
DELETE FROM foreign_flows WHERE date = '2026-08-29';   -- 50 rows: thứ Bảy, không phải phiên
DELETE FROM foreign_flows WHERE ticker = 'VNINDEX';    -- 248 rows: chỉ số nằm trong bảng per-ticker
UPDATE foreign_flows SET buy_value=0, sell_value=0, net_value=0
  WHERE buy_volume=0 AND sell_volume=0 AND (buy_value<>0 OR sell_value<>0);   -- 30 rows legacy raw VND
```

Ổ rác chỉ tồn tại ở date/ticker mà Fireant không trả data → `--migrate` không đè tới được.

### Kết quả sau fix

```
$ python -c "from tools.price import get_foreign_flows; print(get_foreign_flows(days=1).message)"
Khối ngoại 2026-09-04: Mua ròng 286 tỷ đồng
Mua ròng nhiều nhất: VIC 155tỷ / VPB 144tỷ / VHM 132tỷ
Bán ròng nhiều nhất: VCB 125tỷ / HPG 100tỷ
```

```
$ python -c "from agents.intents.price_action import _get_foreign_flow_summary as f; print(f('HPG'))"
Khối ngoại: mua 28 tỷ, bán 100 tỷ, ròng -72 tỷ
```

Data quality sau cleanup:

| Check | Giá trị |
|---|---|
| total rows | 39,538 |
| date range | 2025-08-05 .. 2026-09-04 |
| VNINDEX rows | 0 |
| `abs(net_value) > 10000` tỷ | 0 |
| `volume=0` nhưng `value<>0` | 0 |

Test: `test_phase2.py` + `test_fireant_ingest.py` + `test_phase2_tools.py` → **91 passed, 1 failed**. Fail duy nhất `test_fireant_failure_falls_back_to_vci` là **pre-existing** (verify bằng `git stash` trên HEAD sạch), không liên quan fix này.

### Test fixture sai premise
Hai fixture giả định `close` là VND nên phải sửa cùng:
- `tests/test_fireant_ingest.py:314` — `close: 20_000.0` → `20.0`, expected `/1e9` → `/1e6`
- `tests/test_phase2.py:149` — `_MARKET`/`_BUYERS`/`_SELLERS` từ `e9` → tỷ; thêm `as_of_date="2026-08-25"` cho 4 test DB-path (không có thì `_is_db_fresh()` báo stale theo ngày chạy thật → rơi xuống live VCI → timeout)

---

## Fix 2026-09-05 (phần 2) — phantom session & time-bomb test

### Phantom session — VCI board không có session date

Nghi vấn ban đầu: `ohlcv_daily` thiếu ngày `2026-08-31` (foreign có 147 rows, ohlcv 0). Chẩn đoán sai — thực tế **2026-08-31 không phải phiên giao dịch**.

Bằng chứng 1, Fireant nhảy ngày:

```
$ python -c "... FireantProvider().fetch_history_range('HPG','2026-08-28','2026-09-04')"
      time  close   volume
2026-08-28   22.1 17126700
2026-09-03   21.6 26985600
2026-09-04   21.7 13920900
```

Bằng chứng 2, 143/147 rows trùng khít volume phiên trước:

```sql
SELECT count(*) FROM foreign_flows a
JOIN foreign_flows b ON a.ticker = b.ticker AND b.date = '2026-08-28'
WHERE a.date = '2026-08-31'
  AND a.buy_volume = b.buy_volume AND a.sell_volume = b.sell_volume;
-- → 143
```

Bằng chứng 3, chạy gap-fill trả 0:

```
$ python ingest/fetch_ohlcv.py --all-securities --start-date 2026-08-31 --end-date 2026-08-31
Total: ohlcv=0 foreign=0 rows upserted
```

**Root cause:** VCI price board không mang session date của chính nó. Ngày nghỉ nó vẫn serve số phiên gần nhất, `fetch_live_today()` đóng dấu `target_date` rồi ghi → phantom session. Cùng cơ chế đã sinh ra 50 rows ngày `2026-08-29` (thứ Bảy) đã xoá ở phần 1.

**Fix** (`ingest/fetch_foreign_flows.py`):

| Guard | Chặn được |
|---|---|
| `target_date.weekday() >= 5` → return 0 trước khi gọi API | thứ Bảy/Chủ nhật |
| `_is_stale_board()` — so `(buy_volume, sell_volume)` với phiên đã lưu gần nhất, ≥90% trùng → skip write | ngày lễ, bridge day |

Chọn so volume thay vì dùng holiday calendar vì VN có bridge day bất quy tắc quanh Tết và 2/9 — calendar phải maintain tay, so volume tự đúng.

`_is_stale_board` trả `False` khi `compared < 10`: không đủ overlap thì không đủ cơ sở kết luận, thà ghi còn hơn chặn oan.

Xoá 147 rows phantom `2026-08-31`. Sau đó hai bảng khớp 100%:

```sql
SELECT DISTINCT date FROM foreign_flows EXCEPT SELECT DISTINCT date FROM ohlcv_daily;  -- 0 rows
```

### CLI range cho fetch_ohlcv

Incremental mode đặt `range_start = MAX(date) + 1` → **lỗ ở giữa series không bao giờ được thăm lại**, chỉ `--migrate` (365 ngày × 145 mã) mới lấp được. Thêm `--start-date` / `--end-date` để lấp gap surgical:

```bash
python ingest/fetch_ohlcv.py --all-securities --start-date 2026-08-31 --end-date 2026-08-31
```

Hai flag phải đi cùng nhau (`parser.error` nếu chỉ có một).

### Time-bomb test

`test_fireant_failure_falls_back_to_vci` fail từ 2026-08-31, không liên quan fix đơn vị:

- Fixture `_fireant_api_rows()` hard-code `base = date(2026, 8, 1)` → rows 08-01..08-03
- Test gọi `fetch_and_upsert("HPG", days=30)` → `range_start = today - 30`
- Fallback path (khác Fireant path) **clip** rows theo `[range_start, range_end]`
- Khi today vượt 2026-08-31 → `range_start > 2026-08-03` → drop hết → `ohlcv = 0`

Fireant path không clip nên các test khác dùng cùng fixture vẫn xanh — lỗi chỉ lộ ở đúng một test.

Fix: truyền range tường minh khớp fixture thay vì window tương đối.

### Kết quả

`tests/test_fireant_ingest.py` → **42 passed** (38 cũ + 4 test mới cho weekend guard, stale board, ghi khi volume khác, ngưỡng overlap).

| Check | Giá trị |
|---|---|
| foreign rows | 39,391 |
| range | 2025-08-05 .. 2026-09-04 |
| date có trong foreign mà không có trong ohlcv | none |
| phantom rows 2026-08-31 | 0 |

---

## Step 6 mới trong `scripts/migrate.py` — foreign flows latest session

### Vấn đề

`migrate.py` không bao giờ gọi `ingest/fetch_foreign_flows.py`. Step 5 (`fetch_ohlcv.py --migrate`) populate `foreign_flows` từ Fireant — nhưng Fireant chỉ có foreign **volume**, value phải suy ra `volume × close`. Nên trên môi trường mới, `foreign_flows` chỉ có value xấp xỉ, không có value giao dịch thật.

VCI price board có value thật nhưng **chỉ cho phiên hiện tại** — không backfill lịch sử được. Nên chỉ nâng cấp được phiên gần nhất, phần lịch sử buộc phải chấp nhận value suy ra.

### Thay đổi

Thêm step 6, đẩy market index → 7, stock ratios → 8, audit → 9:

```
[5/9] OHLCV + foreign flows (Fireant->VCI/KBS, 1-year backfill)
[6/9] Foreign flows, latest session (VCI price board)   ← mới
[7/9] Market index daily (SSI iBoard, 365 days)
[8/9] Stock ratios (vnstock KBS → stock_ratios)
[9/9] DB completeness audit
```

Chạy `ingest/fetch_foreign_flows.py --all-securities --live`. Đặt **sau** step 5 để `DO UPDATE` của `_upsert_rows` ghi đè value suy ra bằng value thật — đúng chiều thứ bậc nguồn.

### Non-fatal có chủ ý

`refresh_foreign_latest()` luôn `return True`, chỉ in WARNING khi fail. Ba lý do:

1. VCI là provider foreign duy nhất còn hoạt động (xem BLOCKED.md) — mọi candidate fallback đều 404/DNS fail
2. Ngày nghỉ/cuối tuần step này **cố ý** không ghi gì (weekend guard) → exit 0 nhưng 0 rows
3. Value suy ra từ step 5 đã có sẵn → fail ở đây làm giảm độ chính xác, không làm thiếu data

Không nên để một provider mong manh chặn cả migration lần đầu.

### Verify

```
$ python scripts/migrate.py --dry-run
[6/9] Foreign flows, latest session (VCI price board)
  >>> foreign_flows latest session (VCI)
      [DRY] would run: .../python.exe ingest/fetch_foreign_flows.py --all-securities --live
```

Chạy thật vào thứ Bảy 2026-09-05:

```
[foreign_flows] 2026-09-05 is a weekend — skipping live fetch
LIVE: 2026-09-05 via VCI price board
Done — 0 rows upserted
returned: True
```

DB không đổi sau lần chạy đó: 39,391 rows, `max(date) = 2026-09-04`. Weekend guard và non-fatal wrapper hoạt động đúng cùng nhau.

---

## Step foreign live trong `scripts/daily_ingest.py`

`daily_ingest.py` là đường daily thay-thế-Dagster (chạy tay / cron ngoài, `make ingest-daily`). Trước đó chỉ có 3 step và **không** có foreign live:

```
1. ingest/fetch_ohlcv.py --all-securities   → OHLCV + foreign SUY RA
2. ingest/fetch_index.py --days 5
3. scripts/audit_db.py
```

Sau khi `_upsert_foreign` đổi sang `DO NOTHING`, ai dùng script này thay Dagster sẽ **không bao giờ** nhận value thật — step 1 chỉ lấp lỗ trống, không có gì ghi value VCI vào.

Thêm `run_foreign_live()` vào **cả hai** nhánh (`--migrate` và daily), đặt ngay sau step OHLCV:

```
1. ingest/fetch_ohlcv.py --all-securities        → foreign suy ra (DO NOTHING)
2. ingest/fetch_foreign_flows.py --all-securities --live   ← mới, DO UPDATE
3. ingest/fetch_index.py --days 5
4. scripts/audit_db.py
```

Gọi bằng `run_foreign_live()` chứ không `ok &= run(...)` — non-fatal, cùng lý do như step 6 của `migrate.py`. Fail thì in WARNING ra stderr, exit code của cả script không đổi.

Giờ 3 orchestrator khớp nhau:

| Orchestrator | Foreign value thật | Foreign gap-fill |
|---|---|---|
| Dagster | `foreign_flows_1730` (VCI) | `ohlcv_daily_1830` (Fireant) |
| `scripts/daily_ingest.py` | `run_foreign_live()` | step 1 |
| `scripts/migrate.py` | step `[6/9]` | step `[5/9]` |

### Test

Thêm class `TestForeignLiveOrchestration` (5 test) kiểm tra wiring thay vì chạy network:

- `migrate.refresh_foreign_latest` gọi đúng `--all-securities --live`
- `refresh_foreign_latest` trả `True` cả khi `_run` fail (non-fatal)
- `dry=True` được truyền xuống `_run`
- `daily_ingest.run_foreign_live` gọi đúng command
- `run_foreign_live` không raise / không `sys.exit` khi fail

`tests/test_fireant_ingest.py` → **47 passed** (42 + 5).

### Verify chạy thật

```
$ python -c "from scripts.daily_ingest import run_foreign_live; run_foreign_live()"
[foreign_flows] 2026-09-05 is a weekend — skipping live fetch
LIVE: 2026-09-05 via VCI price board
Done — 0 rows upserted
>>> foreign_flows latest session (VCI price board)
```

Không raise, không ghi gì (thứ Bảy).
