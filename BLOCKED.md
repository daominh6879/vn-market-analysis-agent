# BLOCKED — Việc đang nợ

## Setup

- [x] **Docker bind mounts**: Đã xong. Volumes đổi sang `./data/*`. Cần chạy `docker compose down && docker compose up -d` lần đầu.



## Bài 6

- [x] **Tesseract tiếng Việt**: Đã xong — `vie.traineddata` có sẵn. Dùng `ocr_language="vie+eng"` + `force_ocr=True`. Dấu tiếng Việt đúng.

- [ ] **LlamaParse**: Cần `LLAMA_CLOUD_API_KEY` để so sánh với pymupdf4llm.
  - Đăng ký tại https://cloud.llamaindex.ai (~$0.003/trang)
  - Thú vị nhất là xem LlamaParse xử lý tiêu đề cột BCĐKT có đúng không

- [ ] **unstructured strategy="ocr_only"**: Cần cài poppler trên Windows.
  - Download từ https://github.com/oschwartz10612/poppler-windows
  - Thêm bin/ vào PATH, test lại

- [ ] **VNINDEX matched_value (tổng giá trị khớp lệnh sàn HOSE)**: SSI iBoard chart endpoint (`/v2/stock/second-chart`) không expose total exchange trading value. Cần endpoint riêng (SSI market summary hoặc HOSE feed). Cột `matched_value` trong `market_index_daily` hiện = 0.
  - Thử: `https://iboard-query.ssi.com.vn/v2/market/indices-board` hoặc `https://fc-data.ssi.com.vn/api/v2/market/snapshot?symbol=VNINDEX`

- [ ] **Foreign flows fallback provider**: VCI `price/symbols/getList` là nguồn duy nhất hoạt động. Tất cả candidates fallback đã test đều fail từ machine này:
  - SSI `/v2/stock/foreign-trading` → 404
  - TCBS `apipubaws.tcbs.com.vn/stock-insight/v1/stock/foreign-buy-sell` → 404 (tất cả path thử)
  - CafeF `du-lieu/Ajax/MuaBanNuocNgoai.aspx` → 404
  - VNDirect API → DNS fail
  - VCI IQ `iq.vietcap.com.vn` → market-indices OK, nhưng không có endpoint foreign trading
  - **Hiện tại**: tool `get_foreign_flows` serve data DB (stale) khi VCI live call fail — không crash nhưng data có thể cũ
  - **Cần làm**: Tìm endpoint khác khi có network access tới các source trên, hoặc test từ machine khác

- [x] **`ohlcv_daily` thiếu ngày 2026-08-31** — chẩn đoán sai lúc đầu, **không phải** gap của OHLCV. 2026-08-31 không phải phiên giao dịch: Fireant nhảy từ 2026-08-28 sang 2026-09-03, và 143/147 rows `foreign_flows` ngày đó trùng khít `buy_volume`/`sell_volume` của 2026-08-28. Root cause: VCI price board không mang session date riêng — ngày nghỉ vẫn trả số phiên cũ, `fetch_live_today()` đóng dấu `today()` và ghi thành phantom session. Fix: thêm weekend guard + `_is_stale_board()` trong `ingest/fetch_foreign_flows.py`; xoá 147 rows phantom. Sau fix `foreign_flows` và `ohlcv_daily` khớp 100% (`dates in foreign not in ohlcv: none`).

- [x] **`tests/test_fireant_ingest.py::TestFetchOhlcv::test_fireant_failure_falls_back_to_vci` fail** — time-bomb test. Fixture hard-code ngày `2026-08-01..08-03`, test gọi `fetch_and_upsert(days=30)` → `range_start = today - 30`. Fallback path clip rows theo `[range_start, range_end]` nên khi today vượt 2026-08-31 thì mọi fixture row bị drop → `ohlcv=0`. Fireant path không clip nên các test khác không vỡ. Fix: truyền `start_date`/`end_date` tường minh khớp fixture.
