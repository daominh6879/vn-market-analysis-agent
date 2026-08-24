# Bài 11 — Cửa lọc chất lượng (2026-08-22)

## Files mới

| File | Mục đích |
|------|----------|
| `data/quality.py` | Quality check + quarantine logic |
| `infra/migrations/002_quarantine_log.sql` | Bảng `quarantine_log` |

## Ngưỡng đặt

| Check | Ngưỡng | Lý do |
|-------|---------|-------|
| `chars_per_page` | < 100 | PDF scan |
| `char_ratio` | < 0.30 | OCR tệ / file nhị phân |
| `duplicate_ratio` | > 0.20 | File bị lặp nội dung |

## Baseline — file HPG thật

| File | char_ratio | chars_per_page | Kết quả |
|------|-----------|----------------|---------|
| `0004773662551440329...pdf` (2024, 185 trang) | 0.85 | 1820 | **PASS** |

Cả hai chỉ số cách xa ngưỡng → ngưỡng đặt đúng, file thật không bị chặn nhầm.

## Commands

```bash
make migrate-quarantine                   # tạo bảng quarantine_log
make quality-check FILE=evals/docs/HGP/2024/0004773...pdf  # check 1 file
make quality-list                         # xem danh sách cách ly
```

## TODO — cần test 4 file độc hại

- [ ] PDF scan → phải bị chặn bởi `chars_per_page < 100`
- [ ] File 500 trang → verify không crash, cảnh báo đúng
- [ ] File tiếng Anh → pass (chưa có check ngôn ngữ)
- [ ] File không phải BCTC → pass (chưa có check domain)
