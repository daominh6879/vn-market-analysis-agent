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

## Báo cáo test agent (10/10/2026) — vấn đề dữ liệu chưa xác minh được

Các mục dưới đây là điểm nghi vấn nêu trong `vn_stock_agent_test_report.md` mục B/C. Code đã thêm cảnh báo/ghi chú, nhưng nguyên nhân gốc ở dữ liệu **chưa được kiểm tra** (cần user chạy query/đối chiếu nguồn).

- [ ] **P/E cực thấp/cao (PNJ 3.53, SHB 3.47, CTG 4.65, VIC 153.65)**: nghi EPS TTM chưa điều chỉnh. Agent hiện gắn cảnh báo khi P/E < 5 hoặc > 100 (`agents/intents/fundamentals.py`) nhưng chưa sửa dữ liệu `stock_ratios`. Cần đối chiếu `ingest/fetch_ratios.py` với nguồn thị trường.
- [ ] **Giá HPG/VCB/FPT/VIC/VNM/MWG/ACB** (mục C): cần đối chiếu với nguồn thật (đơn vị nghìn đồng vs đồng, giá chưa điều chỉnh).
- [ ] **CMG P/B 1.04 vs P/E 10.2, ROE 13.4%**: lệch, cần kiểm tra `stock_ratios`.
- [ ] **HDG xếp ngành bất động sản**: cần xem `securities.sector`/`tags` của HDG (Hà Đô thiên về điện).
- [ ] **Báo cáo tài chính theo quý**: `financial_facts` chỉ có kỳ năm. Agent nói rõ giới hạn thay vì suy đoán; cần pipeline ingest quý nếu muốn trả lời "doanh thu quý gần nhất".
- [ ] **Tỷ giá trung tâm SBV**: chưa có nguồn dữ liệu, agent chỉ có tỷ giá ngân hàng.
- [ ] **Cổ tức**: `corporate_events` có thể chưa đầy đủ lịch sử; agent nói rõ "chưa ghi nhận" thay vì kết luận công ty không chi trả.
- [ ] **EPS/ROE theo năm**: tính xấp xỉ từ `financial_facts` (EPS = LNST / số CP niêm yết hiện tại, ROE = LNST / VCSH cuối kỳ), không phải số công bố.

## Khoảng trống dữ liệu (10/10/2026) — code đã có, cần user nạp dữ liệu / còn chặn

- [x] **Nạp dữ liệu cho 8 câu chưa trả lời** — không còn bước thủ công. Migration 038 đã chạy (25 OK, 0 lỗi, 10/10/2026) và 431 securities đã seed lại. Dữ liệu mới do Dagster nạp: asset trong `pipeline/assets_market_data.py` có schedule, và `data_bootstrap_sensor` tự chạy job cho bảng còn rỗng (lúc kiểm tra cả 5 bảng đều rỗng). Cần `dagster dev -f pipeline/assets.py` (hoặc daemon) đang chạy.
- [ ] **Định dạng sự kiện cổ tức của vnstock chưa được kiểm chứng**: `ingest/fetch_dividends.py` đọc số tiền/tỷ lệ bằng regex trên tiêu đề sự kiện (vnstock VCI `Company.events()`), chưa chạy với dữ liệu thật. Asset `stock_dividends` ghi log số sự kiện bị bỏ qua vì không tách được số liệu — xem log lần chạy đầu để kiểm tra; có thể dùng `ingest/fetch_dividends.py --show-schema` để soi cột thô.
- [ ] **Tỷ giá trung tâm NHNN**: `sbv.gov.vn` trả "Request Rejected" (WAF) với request tự động (curl, 10/10/2026). Mirror `tygiausd.org` truy cập được nhưng trang hiển thị ngày 24/04/2025 → bị loại vì quá cũ (`_fetch_central_rate_mirror` kiểm tra ngày ≤ 4 ngày). Agent hiện nói rõ "chưa lấy được". Cần nguồn khác (API chính thức, hoặc tải trình duyệt thật).
- [ ] **Phân ngành PNJ/HDG là phán đoán**: `SECTOR_OVERRIDES` (PNJ→Hàng tiêu dùng, HDG→Tiện ích) chưa đối chiếu nguồn phân ngành chính thức; sửa trong `data/hose_universe.py` nếu muốn khác.
- [ ] **Giá HPG 20.1 / PNJ 19.5 / P/E 3.5**: DB nhất quán nội bộ (P/E = giá ÷ EPS) nên không phải lỗi đơn vị; chưa đối chiếu được với nguồn ngoài.

