# Bài 26 — Daily Market Brief: Phase 4 (Indicator mở rộng + candle + levels)

## Mục tiêu

Mở rộng `calculate_indicators`, thêm `detect_candle_pattern`, thêm `find_support_resistance`.
DoD: với data 250 phiên, output nêu được "trên MA50/MA200, thanh khoản thấp hơn TB 20 tuần ~24%".

---

## Thay đổi

### 1. `tools/price.py` — `calculate_indicators` (mở rộng)

Thêm các chỉ báo mới vào cuối output, giữ nguyên RSI/MACD/MA20/MA50 cũ:

| Chỉ báo | Yêu cầu dữ liệu | Ghi chú |
|---|---|---|
| MA(200) | ≥ 200 phiên | dùng `df.ta.sma(length=200)` |
| EMA(200) | ≥ 200 phiên | dùng `df.ta.ema(length=200)` |
| ADX(14) | ≥ 14 phiên + cột high/low | > 25 = xu hướng mạnh |
| Ichimoku Kumo | ≥ 52 phiên + high/low | giá so Kumo: trên/trong/dưới |
| Volume vs TB | ≥ 5 phiên + cột volume | TB 100 phiên (~20 tuần giao dịch), in % cao/thấp hơn |

Thiết kế: hàm `_safe_float(series)` dùng chung — tránh lặp try/except. Nếu thiếu cột hoặc dữ liệu → in dòng thông báo, không raise.

### 2. `tools/price.py` — `detect_candle_pattern(df)` (mới)

Nhận diện mẫu nến cuối cùng bằng rule thuần (không cần TA-Lib):

| Mẫu | Điều kiện |
|---|---|
| Doji | body_ratio < 0.1 |
| Marubozu xanh/đỏ | body_ratio > 0.85, râu < 10% body |
| Hammer | râu dưới ≥ 2× body, râu trên < 0.5× body, nến xanh |
| Hanging Man | tương tự Hammer nhưng nến đỏ |
| Không xác định | không khớp rule nào |

Trả `ToolResult(data=str)` — tên mẫu.

### 3. `tools/levels.py` — `find_support_resistance(df)` (mới)

Tìm swing high/low bằng rolling window (`window=5`). Lọc:
- **supports**: swing low < giá hiện tại, lấy 3 gần nhất (từ cao xuống)
- **resistances**: swing high > giá hiện tại, lấy 3 gần nhất (từ thấp lên)
- **nearest_round**: mốc tâm lý gần nhất trong `round_levels` (mặc định 1600–2100 bước 50)

Trả `ToolResult(data=dict)` với keys: `supports`, `resistances`, `nearest_round`, `close`.

### 4. Registry

Đăng ký 2 tool mới vào `TOOL_REGISTRY`:
- `detect_candle_pattern`: `cost_hint="free"`, `timeout=5`
- `find_support_resistance`: `cost_hint="free"`, `timeout=5`

### 5. `tools/__init__.py`

Export thêm `detect_candle_pattern`, `find_support_resistance`.

---

## Test cases — `tests/test_phase4.py`

**`TestCalculateIndicatorsExtended`** (14 test):
- MA200 có khi ≥ 200 phiên, "không đủ dữ liệu" khi < 200
- EMA200 có khi ≥ 200 phiên
- ADX có với HLC, báo thiếu cột khi không có high/low
- Ichimoku có với ≥ 52 phiên, báo "không đủ dữ liệu" khi < 52
- Volume ratio ~24% thấp hơn TB — kiểm tra exact text "24%"
- Volume missing column graceful
- Giá trên MA50 khi trending up
- None/empty df → `invalid_input`
- Thiếu cột close → `invalid_input`

**`TestDetectCandlePattern`** (9 test):
- Doji (body_ratio < 0.1)
- Marubozu xanh/đỏ
- Hammer
- Dùng row cuối trong multi-row df
- empty df → `invalid_input`
- thiếu OHLC columns → `invalid_input`
- range = 0 → graceful

**`TestFindSupportResistance`** (10 test):
- Trả ToolResult, status ok
- data dict có đủ keys
- supports < close, resistances > close
- nearest_round với custom round_levels
- Thiếu data → `no_data`
- empty/missing columns → `invalid_input`
- max 3 supports / 3 resistances

---

## Lệnh chạy test

```bash
pytest tests/test_phase4.py -v
```

---

## Quyết định kỹ thuật

- **Không dùng TA-Lib** cho candle pattern — dep phức tạp cài trên Windows. Rule thuần đủ cho 4 mẫu cần thiết.
- **Volume "20 tuần"** = 100 phiên giao dịch (5 ngày/tuần × 20). Dùng `min(100, len-1)` để graceful khi data ngắn.
- **Ichimoku**: dùng `df.ta.ichimoku()` — pandas-ta đã có. Detect cột ISA/ISB bằng `startswith` vì tên cột có thể thay đổi theo version pandas-ta.
- **find_support_resistance**: không dùng clustering phức tạp — swing point window đủ cho DoD. Mốc tâm lý default range VN-Index 1600-2100 step 50.
