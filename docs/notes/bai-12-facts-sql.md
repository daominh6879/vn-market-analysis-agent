# Bài 12 — Hai đường dữ liệu: số không vào vector DB

## Files tạo mới

| File | Mục đích |
|------|----------|
| `infra/migrations/003_financial_facts.sql` | Bảng `financial_facts` + `stock_prices` |
| `data/extract_facts.py` | Trích xuất structured output → Postgres |
| `data/fetch_prices.py` | Tải giá cổ phiếu (vnstock, close_adj) |

## Thiết kế

**"Số không đi qua model, chỉ câu SQL đi qua"** — Claude chỉ làm parser:
markdown table → JSON schema. Số đã vào DB thì chỉ truy vấn bằng SQL.

```
markdown → DeepSeek (tool use, strict schema) → FinancialFact[] → Postgres
user query → SQL → số thật → trả về
```

## Schema bảng

```sql
financial_facts: ticker, ky, loai_bao_cao, ma_chi_tieu, gia_tri, don_vi, nguon_file, nguon_trang
stock_prices:    ticker, ngay, close_adj (giá đã điều chỉnh), volume
```

`UNIQUE(ticker, ky, loai_bao_cao, ma_chi_tieu)` → idempotent upsert.

## Ba validator nghiệp vụ

| Validator | Ngưỡng | Lý do |
|-----------|--------|-------|
| `mixed_report_type` | riêng lẻ ≠ hợp nhất trong 1 batch | So sánh không hợp lệ |
| `balance_sheet_mismatch` | chênh > 1% | Tổng TS = Nợ PT + VCSH |
| `inconsistent_value` | thay đổi > 500% giữa kỳ | OCR bị sai đơn vị |

## Số HPG 2024 (riêng lẻ) — dùng để đối chiếu PDF trang 7–8

| Chỉ tiêu | Giá trị (VND) |
|----------|--------------|
| tong_tai_san | 81.793.076.515.644 |
| no_phai_tra | 1.012.889.937.592 |
| von_chu_so_huu | 80.780.186.578.052 |
| doanh_thu_thuan | 336.838.497.852 |
| loi_nhuan_sau_thue | 10.247.400.472.100 |

Kiểm tra balance sheet: 1.012.889.937.592 + 80.780.186.578.052 = **81.793.076.515.644** ✓

## Commands

```bash
# 1. Tạo bảng
make migrate-facts

# 2. Thử extract (xem kết quả, không insert)
make extract-facts-dry FILE=outputs/2024/hpg_pymupdf.md

# 3. Extract và insert
make extract-facts FILE=outputs/2024/hpg_pymupdf.md

# 4. Verify — SELECT ra đúng số
make query-fact MA=tong_tai_san KY=2024
# → phải ra 81793076515644

# 5. Test validator với số sai
python -c "
from data.extract_facts import FinancialFact, validate_facts
bad = [
    FinancialFact(ticker='HPG', ky='2024', loai_bao_cao='rieng_le',
                  ma_chi_tieu='tong_tai_san', gia_tri=999999,
                  don_vi='VND', nguon_file='test', nguon_trang=7),
    FinancialFact(ticker='HPG', ky='2024', loai_bao_cao='rieng_le',
                  ma_chi_tieu='no_phai_tra', gia_tri=500000,
                  don_vi='VND', nguon_file='test', nguon_trang=7),
    FinancialFact(ticker='HPG', ky='2024', loai_bao_cao='rieng_le',
                  ma_chi_tieu='von_chu_so_huu', gia_tri=400000,
                  don_vi='VND', nguon_file='test', nguon_trang=7),
]
errors = validate_facts(bad)
assert any(e.type == 'balance_sheet_mismatch' for e in errors)
print('Validator hoạt động:', [e.type for e in errors])
"

# 6. Tải giá cổ phiếu (cần uv add vnstock trước)
make fetch-prices TICKER=HPG FROM=2022-01-01 TO=2024-12-31
```

## Bug đã sửa

| Bug | Nguyên nhân | Fix |
|-----|-------------|-----|
| Model chỉ trả 1 fact | Tool schema dùng `input_schema` (Anthropic format) thay vì `parameters` (OpenAI/DeepSeek format) | Đổi key |
| Cắt mất BCĐKT | `max_chars=10_000` cắt từ đầu file — phần BCĐKT ở trang 6–8 nằm sau auditor report | `_extract_financial_section()` tìm header trước rồi cắt |

## Kết quả

- `tong_tai_san 2024`: **81.793.076.515.644 VND** — khớp PDF trang 7 ✓
- `validator('balance_sheet_mismatch')`: bắt được số sai ✓

## TODO — Checklist hoàn thành

- [X] `make migrate-facts` chạy thành công
- [X] `make extract-facts-dry FILE=outputs/2024/hpg_pymupdf.md` in ra số đúng
- [X] `make extract-facts FILE=outputs/2024/hpg_pymupdf.md` insert thành công
- [X] `make query-fact MA=tong_tai_san KY=2024` → 81793076515644 (khớp PDF trang 7)
- [X] Test validator bắt số sai → `balance_sheet_mismatch`
- [X] `uv add vnstock` + `make fetch-prices` (optional, cần kết nối internet)
