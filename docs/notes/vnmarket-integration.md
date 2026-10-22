# Tích hợp vnmarket (thay nguồn dữ liệu)

Cài: `pip install -e ../vnmarket` (đã thêm `-e ../vnmarket` vào `requirements.txt`). Client dùng chung: `core/vnmarket_client.py` (`get_client()`).

## Quyết định
- Chọn "Replace existing sources": thay vnstock vendored và các scraper `data/*.py` bằng vnmarket; giữ nguyên chữ ký hàm và tên class để caller/test không đổi.

## Đã thay bằng vnmarket
| File | Thay đổi |
|------|----------|
| `data/fx_scraper.py` | `fetch_vcb_usdvnd()` dùng `vn.macro.fx()` (tỷ giá trung tâm SBV giữ nguyên) |
| `data/gold_vn_scraper.py` | SJC qua `vn.macro.gold("sjc")`, dòng "SJC 1L" HCM, đổi VND → triệu đồng; fallback yfinance giữ nguyên |
| `data/cafef_rss.py` | `fetch_rss_news` → `vn.news.latest`; `fetch_ticker_news` → `vn.company.news` |
| `tools/providers.py` | `FireantProvider`, `VciDirectProvider`, `KbsProvider` giữ tên, chạy trên vnmarket; `SsiIndexProvider` giữ (vnmarket thiếu giá trị khớp lệnh) |
| `ingest/fetch_prices.py` | `vn.prices.history`, chia 1000 (bảng `stock_prices` lưu nghìn đồng) |
| `ingest/fetch_index.py` | HNX/UPCOM qua `HNXINDEX`/`UPCOMINDEX`; thành phần chỉ số qua `vn.listing.symbols` |
| `data/hose_universe.py` | `vn.listing.all()` + `vn.listing.sectors()`; sector cũ trong cache được giữ, ICB chỉ phân loại mã mới |
| `ingest/fetch_shares.py` | `company.profile()["listed_shares"]` rồi `company.overview()["issue_share"]` |
| `ingest/fetch_ohlcv.py`, `ingest/fetch_foreign_flows.py` | ưu tiên `foreign_buy_value`/`foreign_sell_value` thật, fallback volume × close |

Tool mới (`tools/vnmarket_tools.py`, đăng ký trong `tools/registry.py` và `tools/mcp_server.py`): `get_live_quotes`, `get_intraday_ticks`, `get_vn_index_board`, `get_live_sector_performance`, `get_proprietary_trading`, `get_company_profile`.

## Thay đổi hành vi
- `data/hose_tickers.json` sinh lại: 406 mã, thêm ECO, `company_name` đổi sang tên pháp lý (`name_vi`), 3 mã đổi sector theo HOSE_SEED (HDG, PNJ, VRE).
- Giá vnmarket là VND; `stock_prices` vẫn là nghìn đồng.

## Tài chính, tỷ số (đợt 2)
| File | Thay đổi |
|------|----------|
| `ingest/fetch_financials.py` | `vn.finance.statement(labels=False)` → map theo **mã trường VCI IQ** (ổn định giữa ngân hàng và phi ngân hàng: `isa3`→`doanh_thu_thuan`, `isa5`, `isa16`, `isa20`, `bsa53`, `bsa54`, `bsa78`, `bsa80`→`von_dieu_le`, `cfa18`…). Mã còn lại: `slugify(title_vi)` từ `finance.metrics`; cột không có tiêu đề bị bỏ. Kỳ quý nhãn `Q3/2024`. `source='vnstock'` giữ nguyên làm nhãn cũ (`extract_facts.query_fact` ưu tiên theo nhãn này), `source_file='vnmarket_<báo cáo>'`. Tham số `--source` còn nhưng bị bỏ qua. |
| `ingest/fetch_ratios.py` | `ratio_row()` (dùng chung với `agents/intents/fundamentals.py`) lấy dòng TTM mới nhất của `finance.ratios`: pe/pb/roe/roa/biên đã ở giá hiện tại nên bỏ `_reprice`; roe/roa/biên ×100; `eps = market_cap / shares / pe` (vnmarket không có EPS); tăng trưởng doanh thu / LNST mẹ tính YoY từ 2 năm báo cáo gần nhất (`isa3`, `isa22`); giá trị 0.0 coi là không áp dụng → NULL. Lịch sử theo năm (`ratio_*` trong `financial_facts`) lấy từ dòng `RATIO_YEAR`. |
| `agents/intents/fundamentals.py` | `_fetch_from_vnstock` → `_fetch_live`, gọi `ratio_row`; nhãn nguồn đổi thành vnmarket. |
| `scripts/migrate.py` | bước 8 gọi `fetch_and_upsert` thay vì bản sao logic KBS. |

Thay đổi ngữ nghĩa cần biết: tỷ số trước đây là năm tài chính gần nhất (KBS, re-price); nay là TTM theo giá hiện tại. Ngân hàng không có `doanh_thu_thuan` (như trước), nên `revenue_growth_pct` NULL.

**Đối chiếu với DB (2026-10-14, HPG/VCB/FPT, năm 2020–2025, `source='vnstock'`)**: 2.131 dòng trùng khóa giữa `financial_facts` cũ và dữ liệu vnmarket, chênh lệch giá trị = 0. Lần chạy đầu có 15–18 dòng lệch mỗi mã: `hang_ton_kho` (đã map nhầm `bsa15` là hàng tồn kho ròng, nay bỏ để `bsa16` → `hang_ton_kho`, `bsa15` → `hang_ton_kho_rong`) và các tiêu đề trùng giữa nhiều mã trường (nay bỏ qua slug mơ hồ). Mỗi mã còn 18–30 dòng chỉ có ở bản cũ (tên metric KBS khác, ví dụ `lailo_thuan_sau_thue`) và 30–36 dòng chỉ có ở bản mới (ví dụ HPG có thêm `loi_nhuan_sau_thue`, `von_dieu_le`, `dong_tien_hoat_dong`); các dòng cũ không bị xóa.

`stock_ratios` (HPG/VCB/FPT): `revenue_growth_pct`, `earnings_growth_pct` khớp bản lưu đến 2 chữ số; `pe`, `pb`, `roe`, `roa`, biên, `eps`, `ev_ebitda` khác do nay là TTM theo giá hiện tại (ví dụ HPG P/E 10,39 → 7,44, ROE 12,69% → 17,38%). `de_ratio` dùng `debt_per_equity` (nợ vay / vốn chủ, khớp ngữ nghĩa cũ: HPG 0,70) chứ không phải `debt_to_equity` (tổng nợ / vốn chủ, 0,97).

## Cổ tức và sự kiện doanh nghiệp (đã thay, bỏ hẳn vnstock)
vnmarket thêm 2 API trên nguồn VCI IQ `/v1/events`: `vn.company.events(symbol, start, end, codes, limit)` và `vn.market.events(start, end, codes, limit)` (lịch cả thị trường, có cột `ticker`). API nhận page size ≥ 2000 nên không cần phân trang.

| File | Thay đổi |
|---|---|
| `ingest/fetch_dividends.py` | `fetch_events_df` gọi `company.events(t, codes="DIV,ISS")`; `NoData` → `None`. Phải lọc `codes`: mặc định API trả cả giao dịch nội bộ (DDRP/DDIND/DDINS) và `limit` cũ 50 làm HPG chỉ còn 2 dòng DIV. `source` đổi thành `vnmarket_vci`. |
| `data/corporate_events_scraper.py` | `fetch_vnmarket_events` gọi `market.events`; `scrape_events` dùng vnmarket trước, CafeF HTML làm dự phòng; bỏ scraper TCBS. |
| `pipeline/assets_market_brief.py` | `corporate_events_ingest` raise khi 0 sự kiện. |
| `pipeline/assets_market_data.py` | thêm kiểm tra bootstrap `corporate_events` vào `data_bootstrap_sensor`; bỏ các `time.sleep` (vnmarket đã throttle theo nguồn). |

Ánh xạ `event_code` (VCI) → `corporate_events.event_type`: `DIV`→`dividend`, `ISS`→`rights_issue`, `AGME`/`AGMR`/`EGME`→`agm`. Các mã còn lại (giao dịch cổ đông lớn/nội bộ DDIND/DDINS/DDRP, niêm yết bổ sung AIS, SUSP, MOVE, OTHE…) không lưu. `ex_date` = ngày GDKHQ (`exright_date`; sự kiện đã công bố nhưng chưa có ngày GDKHQ thì bỏ qua); với `agm` là ngày họp (`display_date1`). `ratio` lấy từ tiêu đề (`_extract_ratio`), thiếu thì `exercise_ratio × 100` cho `rights_issue`. Loại `gdkhq` không còn được sinh ra (VCI không tách riêng).

Dự phòng khi VCI IQ lỗi: ngày 2026-10-14 `iq.vietcap.com.vn` nhận kết nối TCP/TLS nhưng không trả byte nào (cả 3 IP CDN, `ReadTimeout`), `fetch_dividends` toàn bộ mã không có dữ liệu. vnmarket thêm `vnd.events` (VNDirect `api-finfo.vndirect.com.vn/v4/events`) vào `Fallback` của `company.events`/`market.events`, trả cùng dạng khung với VCI. Ánh xạ: `DIVIDEND`→`DIV` (`value_per_share`=`dividend`, `payout_date`=`expiredDate`), `STOCKDIV`/`KINDDIV`→`DIV`, `ISSUE`→`ISS` (`exercise_ratio`=`ratio`/100), `MEETING`→`AGME`, `meetingRight`→`AGMR`; các loại khác bị bỏ. Đã kiểm với HPG: 2026-05-11 tiền 500đ, 2026-05-25 cổ phiếu 10% khớp bảng `dividends` (nguồn VCI). Giới hạn đã đo: lịch sử HPG qua VNDirect chỉ 6 dòng (từ 2022) so với 13 dòng của VCI (từ 2017); lịch thị trường 2026-10-13…28 có DIV 24, AGME 18, AGMR 6, ISS 2 (VCI trước đó ISS 8). Không có `record_date` từ nguồn này.

## Kiểm thử
- Đã bỏ `vnstock` khỏi `requirements.txt` và `Dockerfile.api`; `vendor/vnmarket` (gitignored) cần đồng bộ lại từ `../vnmarket/src/vnmarket` khi build image.
- Test mới: `tests/test_vnmarket_tools.py` (10), `tests/test_fetch_prices_vnmarket.py` (2); `tests/test_fireant_ingest.py` 49 pass.
- Kiểm tra live: HPG foreign flow 2026-10-09..13 khớp bảng `foreign_flows` trong Postgres; `listed_shares` HPG 8,442,964,520; SJC 1L mua 141.3 / bán 144.3.
- `tests/test_minio_pipeline.py` và `tests/test_pipeline.py::test_new_pdf_indexed` lỗi do MinIO; test router/memory/e2e dùng LLM thật lỗi do DeepSeek `402 Insufficient Balance` (xem BLOCKED.md).

Repro: `pytest tests -m "not live" --ignore=tests/test_fetch_live.py`

## Khối ngoại HOSE chậm → circuit breaker (2026-10-21)
- Triệu chứng: run `ohlcv_ingest_job` bù tick 18:30 chạy 30–75 giây/mã (432 mã → ước tính vài giờ). Các run trước (12–16/10) mất 12–20 phút cho cả lượt.
- Đo riêng 1 mã: giá (`vn.prices.history`) 0,6–2 giây; khối ngoại (`vn.market.foreign`) HPG 22 giây, VCB 121 giây. Log: `hose failed: transport error ReadTimeout` rồi `fallback: cafef answered after hose failed`. Endpoint HOSE timeout 20 giây × 4 lần thử (retries=3) + backoff.
- Không đổi thứ tự sang CafeF trước: so HOSE với CafeF (FPT 06–21/10), CafeF làm tròn lô 100 (thiếu lô lẻ, lệch vài trăm cp/ngày) và thiếu một phần giao dịch thoả thuận — HOSE cộng `bigLot*`; ngày 14/10 CafeF thiếu 235.031 cp ở cả mua lẫn bán, có ngày ròng lệch tới 3,5 tỷ.
- Sửa trong `../vnmarket`:
  - `_core.py`: circuit breaker trong `Fallback` — nguồn có ≥ 3 lỗi liên tiếp (`Health` status `down`) bị bỏ qua `Config.breaker_cooldown` = 600 giây, sau đó gọi thử 1 lần; endpoint cuối trong `Fallback` không bao giờ bị bỏ qua; `breaker_cooldown=0` tắt.
  - `sources/hose.py`: `foreign` timeout 10 giây (mặc định 20).
  - `pytest tests/test_core.py` → 25 passed (2 test mới).
- Đo thật 12 mã liên tiếp: 8 mã đầu 26–62 giây/mã (đến khi HOSE đủ 3 lỗi liên tiếp), sau đó 0,7–1,0 giây/mã. Run mới `c5e40587`: 291 mã trong 20 phút 17 giây (21:18:02–21:38:19, ~4,2 giây/mã gồm `time.sleep(1.1)`), 1 mã lỗi (`FC1` — chứng chỉ quỹ, không nguồn nào có nến). Sau run: `ohlcv_daily` có 267 mã ngày 20/10, 270 mã ngày 21/10; HPG 21/10 = 21,15.
- Run bị dừng ở mã thứ 291 vì Claude Code tắt tiến trình nền `dagster dev` khi máy thiếu RAM — không phải lỗi của job. Các mã còn lại cập nhật ở run kế tiếp.
- Docker: image API dùng `vendor/vnmarket` → cần đồng bộ lại từ `../vnmarket/src/vnmarket` khi build.
