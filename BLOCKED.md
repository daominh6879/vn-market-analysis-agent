# BLOCKED — Việc đang nợ

## Setup

- [x] **Docker bind mounts**: Đã xong. Volumes đổi sang `./data/*`. Cần chạy `docker compose down && docker compose up -d` lần đầu.

- [x] **BCTC PDF chưa có trong MinIO (2026-10-27)**: Đã xong — asset `bctc_sync` + schedule tuần + bootstrap; HPG (168 chunk) và VCB (287 chunk) đã nạp. Chi tiết: `docs/notes/bctc-sync.md`.
- [ ] **CafeF trả link PDF 404 (2026-10-27)**: 6 trong 14 mã đã thử bị 404 ở link PDF (FPT, VNM, ACB, SSI, GAS, MSN; mẫu do tôi chọn, không ngẫu nhiên). `bctc_sync` bỏ qua các mã này (run vẫn SUCCESS, lỗi chỉ nằm trong log). Cần nguồn BCTC dự phòng; tỷ lệ trên toàn bộ 431 mã chưa biết.



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

- [x] **P/E không khớp EPS × giá (PNJ, SHB, CTG, VIC, HPG, FPT…)**: root cause đã xác minh — `stock_ratios` còn số thô của KBS (ghi trước khi có `_reprice` trong `ingest/fetch_ratios.py`): 348/409 mã có P/E×EPS lệch giá đóng cửa >5%. Fix: làm mới 430/431 mã bằng `python -m ingest.fetch_ratios`; sau đó chỉ còn 3 mã lệch (BCG, HID, TCO) do `ohlcv_daily` không có giá ≤ 31/12/2024 → `_reprice` thêm nhánh dự phòng (P/E = giá hiện tại ÷ EPS, P/B = NULL). Fallback live trong `fundamentals.py` cũng dùng `_reprice` nên số live và số DB khớp. Bảng định giá ghi ngày cập nhật.
- [ ] **Giá HPG/VCB/FPT/VIC/VNM/MWG/ACB** (mục C): cần đối chiếu với nguồn thật (đơn vị nghìn đồng vs đồng, giá chưa điều chỉnh).
- [ ] **CMG P/B 1.04 vs P/E 10.2, ROE 13.4%**: lệch, cần kiểm tra `stock_ratios`.
- [ ] **HDG xếp ngành bất động sản**: cần xem `securities.sector`/`tags` của HDG (Hà Đô thiên về điện).
- [x] **Báo cáo tài chính theo quý**: asset `financials_quarterly` đã nạp kỳ `Qn/YYYY` (FPT có Q3/2025–Q2/2026, doanh thu Q2/2026 17.225 tỷ, LNST 2.901 tỷ). Schema prompt của `rag/sql_agent.py` đã thêm mã `loi_nhuan_sau_thue` của dữ liệu quý và ví dụ truy vấn quý mới nhất.
- [x] **Tỷ giá trung tâm SBV**: lấy từ biểu đồ trên trang chủ `www.sbv.gov.vn` (`tyGiaLabels`/`tyGiaValues` có ngày) — `_fetch_sbv_homepage_chart` trong `data/fx_scraper.py`; 25.629 đ ngày 10/10/2026; có điểm trước đó nên trả lời được "so với phiên trước".
- [x] **Cổ tức**: bảng `dividends` có 2.773 dòng (VNM: 1.850/350/2.000/500 đ/cp; HPG: cổ phiếu 10–30% + tiền mặt 500 đ). Text-to-SQL trước đó không biết bảng này và `rag_readonly` thiếu quyền SELECT → đã thêm vào `ALLOWED_TABLES`/schema prompt và GRANT trong `004_readonly_role.sql`.
- [ ] **EPS/ROE theo năm**: tính xấp xỉ từ `financial_facts` (EPS = LNST / số CP niêm yết hiện tại, ROE = LNST / VCSH cuối kỳ), không phải số công bố.

## Khoảng trống dữ liệu (10/10/2026) — code đã có, cần user nạp dữ liệu / còn chặn

- [x] **Nạp dữ liệu cho 8 câu chưa trả lời** — không còn bước thủ công. Migration 038 đã chạy (25 OK, 0 lỗi, 10/10/2026) và 431 securities đã seed lại. Dữ liệu mới do Dagster nạp: asset trong `pipeline/assets_market_data.py` có schedule, và `data_bootstrap_sensor` tự chạy job cho bảng còn rỗng (lúc kiểm tra cả 5 bảng đều rỗng). Cần `dagster dev -f pipeline/assets.py` (hoặc daemon) đang chạy.
- [x] **Định dạng sự kiện cổ tức của vnstock** — đã kiểm chứng bằng dữ liệu thật (VNM, HPG đọc đúng tiền/tỷ lệ). Mục cũ:: `ingest/fetch_dividends.py` đọc số tiền/tỷ lệ bằng regex trên tiêu đề sự kiện (vnstock VCI `Company.events()`), chưa chạy với dữ liệu thật. Asset `stock_dividends` ghi log số sự kiện bị bỏ qua vì không tách được số liệu — xem log lần chạy đầu để kiểm tra; có thể dùng `ingest/fetch_dividends.py --show-schema` để soi cột thô.
- [x] **Tỷ giá trung tâm NHNN** — `sbv.gov.vn` chặn `curl` nhưng trang chủ truy cập được bằng User-Agent trình duyệt và có biểu đồ tỷ giá; xem mục SBV ở trên. Nguồn dự phòng `tygiausd.org` vẫn bị loại vì ngày 24/04/2025.
- [ ] **Phân ngành PNJ/HDG là phán đoán**: `SECTOR_OVERRIDES` (PNJ→Hàng tiêu dùng, HDG→Tiện ích) chưa đối chiếu nguồn phân ngành chính thức; sửa trong `data/hose_universe.py` nếu muốn khác.
- [ ] **Giá HPG 20.1 / PNJ 19.5 / P/E 3.5**: DB nhất quán nội bộ (P/E = giá ÷ EPS) nên không phải lỗi đơn vị; chưa đối chiếu được với nguồn ngoài.


## Còn lại sau đợt sửa 11/10/2026

- [ ] **Ollama phải chạy** (`localhost:11434`, model `bge-m3`): thiếu thì truy xuất RAG và episodic memory không embed được. Đã che lỗi kỹ thuật khỏi người dùng (`rag/qa.py::_service_error`); `tests/test_episodic_memory.py` tự skip khi Ollama/Qdrant không chạy.
- [ ] **EPS của BCG/HID/TCO**: KBS chỉ có năm tài chính 2024 nên P/E chỉ là giá hiện tại ÷ EPS 2024, P/B để trống.

## Giá hôm nay (12/10/2026)

- [x] **TCBS `bars-long-term` trả 404**: `TcbsDirectProvider._fetch_ohlcv` (tools/providers.py) lỗi `404 Not Found` cho HPG (probe 12/10 12:04). Chỉ là fallback cuối sau Fireant → VCI nên chưa ảnh hưởng kết quả; cần endpoint TCBS mới hoặc bỏ khỏi chain. Giải quyết: chain fallback giá đổi từ Fireant → VCI → TCBS thành Fireant → VCI → KBS (`KbsProvider`, đã kiểm tra trả nến 12/10, đơn vị VND đúng).
- [x] **Giá HPG trả phiên 09/10 thay vì hôm nay**: `_is_db_fresh` coi DB phiên thứ Sáu là fresh cả ngày thứ Hai (DB nạp 18:00). Đã sửa: trong ngày giao dịch sau 09:00 DB phải có nến hôm nay, nếu không thì gọi API live (Fireant/VCI đều trả nến hôm nay).

## Tỷ giá trung tâm (quan sát 12/10/2026)

- [ ] **`fx_scraper` không lấy được tỷ giá trung tâm SBV**: log khi chạy eval: `SBV homepage chart failed: Client error '403 No server' for url 'https://www.sbv.gov.vn/'` và `central-rate mirror is stale (2025-04-24); ignored`. Câu hỏi tỷ giá trung tâm hiện dựa vào nguồn còn lại; cần nguồn/endpoint mới hoặc header/proxy cho SBV.

## Intent model (12/10/2026) — chi tiết ở `docs/notes/intent-model.md`

- [ ] **Chưa có kết quả fine-tune**: `intent_model/finetune.py` (multilingual-e5-small) đang chạy trên dữ liệu đã lọc; chưa có số để quyết định có nối cascade vào `llm_route` hay không. Baseline đông cứng chỉ đạt 78,7% intent trên tập gold (50% câu được phục vụ ở ngưỡng 0,8 với độ chính xác 97,4%).
- [ ] **`SBV` mơ hồ**: "Fed và SBV" là Ngân hàng Nhà nước nhưng `extract_focus_entities` trả `['SBV']` (mã Siam Brothers có thật trong universe). Không phân biệt được bằng từ vựng; cần ngữ cảnh (ví dụ "lãi suất", "NHNN") hoặc bỏ khỏi nhóm mã khi đi cùng "Fed".
- [ ] **Thiếu alias tên công ty**: đã thêm "vn milk" vào `TICKER_ENRICHMENT["VNM"]` (`data/hose_universe.py`, có test `test_vn_milk_alias_is_curated_for_vnm`); **DB chưa được seed lại** nên `securities.name_aliases` chưa có alias này cho tới khi chạy `python ingest/seed_securities.py` (hoặc asset Dagster seed). "nhà bán lẻ này", "vp b" mơ hồ, không thêm được.
- [ ] **Bộ lọc `screening` chưa mô hình hoá**: ghép đúng chỉ báo–toán tử–ngưỡng cho nhiều bộ lọc cần token tagging; cascade phải rơi về LLM với `screening` cho đến khi có.
- [ ] **Nhãn synthetic chưa đối chiếu với router thật**: quy tắc lọc nhiễu (`common.load_synthetic`) là cách đọc `ROUTING_RULES`; chưa chạy `llm_route` trên 155 câu seed để xác nhận các nhãn viết tay còn tranh cãi.

- [ ] DeepSeek API trả `402 Insufficient Balance` (xác nhận 2026-10-14 bằng `create_client().generate(...)`). `llm_classify` nuốt exception và trả `conversation`, nên các test router/memory/e2e dùng LLM thật fail (test_router_intents_cache 79, test_regression_fixes 5, test_observability_streaming_e2e 3, test_conversation_memory 1, test_episodic_memory 1). Cần nạp tiền tài khoản DeepSeek rồi chạy lại các file này.

- [x] vnmarket không có nguồn cổ tức / sự kiện doanh nghiệp → đã thêm `company.events` và `market.events` (VCI IQ `/v1/events`) vào vnmarket; `fetch_dividends.py` và `corporate_events_scraper.py` đã chuyển sang vnmarket, bỏ phụ thuộc `vnstock` (2026-10-14).
- [x] Đợt chuyển `fetch_financials` / `fetch_ratios` sang vnmarket đã được đối chiếu với DB thật ngày 2026-10-14 (xem docs/notes/vnmarket-integration.md); chưa chạy job Dagster ghi dữ liệu mới.

- [ ] Đồng bộ TCBS (`POST /portfolio/{user_id}/tcbs/sync`) chưa chạy với TCBS thật: cần `TCBS_API_KEY` (tạo trên TCInvest), `TCBS_ACCOUNT_NO` (tiểu khoản thường) trong `.env` và Smart OTP tại thời điểm gọi. Docs: https://developers.tcbs.com.vn/docs/v1.0.0/introduction/ (2026-10-19). Chưa xác nhận trường hợp tiểu khoản margin và thời hạn JWT (docs không nêu).
