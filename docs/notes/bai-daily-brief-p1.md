# Bài 25 — Daily Market Brief: Phase 1 (HOSE universe + breadth + top movers)

## Mục tiêu

Mở rộng `get_market_breadth` từ VN30 (30 mã) → toàn sàn HOSE (~150 mã seed, có thể mở rộng lên ~400). Thêm `get_top_movers` để lấy trụ đỡ/dẫn dắt thanh khoản. Tiếp nối từ bài 24.

---

## Vấn đề cũ

`get_market_breadth()` hardcode `_VN30_CONSTITUENTS` (30 mã). Báo cáo thật cần 246 tăng / 439 giảm — tức breadth toàn sàn HOSE, không phải VN30.

---

## Giải pháp

### 1. `data/hose_universe.py`

Hai tầng:
- **HOSE_SEED**: ~150 mã hardcoded với sector tag + index membership (VN30/VN100/HOSE). Luôn có sẵn, không cần mạng.
- **`fetch_and_save_hose_universe()`**: gọi VCI endpoint để lấy full list ~400 mã, lưu `data/hose_tickers.json`. Chạy định kỳ để refresh.
- **`load_hose_tickers()`**: đọc từ cache JSON, fallback về seed.

```bash
# Refresh cache từ VCI
python data/hose_universe.py
```

Seed phân bổ ngành:

| Ngành | Số mã |
|-------|--------|
| Ngân hàng | 20 |
| Bất động sản | 16 |
| Chứng khoán | 10 |
| Logistics | 12 |
| Vật liệu/Thép | 6 |
| Dầu khí/Năng lượng | 9 |
| Bán lẻ/FMCG | 14 |
| Công nghệ | 4 |
| Y tế | 5 |
| Khác | ~54 |

### 2. `get_market_breadth(universe="HOSE"|"VN30")`

```python
# Mặc định HOSE
result = get_market_breadth()                  # HOSE ~150 mã
result = get_market_breadth(universe="VN30")   # VN30 30 mã (nhanh hơn)
```

Thay đổi:
- Nhận `universe` param (mặc định `"HOSE"`)
- DB-first: `query_universe_latest(tickers)` — 1 SQL JOIN
- Fallback: VCI batch chia chunk 30 mã (rate-limit safe)
- Label trong output: "HOSE breadth: X tăng / Y đứng / Z giảm"

### 3. `get_top_movers(by, limit)`

Tool mới:

```python
get_top_movers(by="value",    limit=5)  # top thanh khoản (close×volume)
get_top_movers(by="pct_gain", limit=5)  # top tăng giá
get_top_movers(by="pct_loss", limit=5)  # top giảm giá
```

DB-first. `by="value"` dùng `query_top_by_value()` (ORDER BY close×volume DESC). Dùng để xác định "trụ đỡ" như VIC 2.300 tỷ trong report.

### 4. `ohlcv_db.py` mở rộng

- `query_universe_latest` = alias của `query_vn30_latest` (logic giống nhau, tên rõ hơn)
- `query_top_by_value(tickers, limit)`: SELECT + ORDER BY `close * volume` DESC

### 5. `ingest/fetch_ohlcv.py`

Thêm flag `--universe hose|vn30`:

```bash
# Backfill toàn HOSE seed
python ingest/fetch_ohlcv.py --universe hose --days 30

# Chỉ VN30
python ingest/fetch_ohlcv.py --universe vn30 --days 60
```

---

## DoD verify

```bash
# Sau khi backfill ohlcv_daily với HOSE seed:
python -c "
from tools.price import get_market_breadth, get_top_movers
r = get_market_breadth()
print(r.message)   # HOSE breadth: X tăng / Y đứng / Z giảm

r2 = get_top_movers(by='value', limit=5)
print(r2.message)  # top 5 thanh khoản
"
```

---

## Tests

```bash
python -m pytest tests/test_p1_breadth_movers.py -v
# 18 tests, all pass
```

Coverage:
- `HOSE_SEED` không có ticker trùng
- `load_hose_tickers()` trả về >100 tickers
- `get_market_breadth(universe="HOSE")` đếm đúng advances/declines từ DB mock
- `get_top_movers(by="value")` trả ticker đứng đầu theo traded_value
- `get_top_movers(by="pct_gain")` sort đúng thứ tự giảm dần
- `get_top_movers(by="random")` trả `invalid_input`
- `fetch_and_save_hose_universe()` trả 0 khi network lỗi

---

## Còn thiếu

- Seed chỉ có ~150 mã → breadth thật cần ~400 mã. Cần chạy `fetch_and_save_hose_universe()` thành công với VCI endpoint để có cache đầy đủ.
- `query_top_by_value` dùng `close * volume` làm proxy cho traded value (đơn vị VND). Thực tế sàn dùng giá trị khớp lệnh thực. Sai số nhỏ (close ≈ matched price), chấp nhận được.
- Sau khi có `get_top_movers`, cần cập nhật agent `synthesize` prompt để đề cập trụ đỡ.

---

## Phase tiếp theo

Chọn một trong ba hướng:
- **P4**: thêm MA200/EMA200/ADX/Ichimoku vào `calculate_indicators`
- **P2**: foreign flows (khối ngoại mua/bán ròng)
- **P6**: nhảy thẳng vào `market_brief_graph` + template (bản tin end-to-end)
