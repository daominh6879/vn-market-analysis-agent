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

**Chưa kiểm tra với DB**: lúc làm đợt này Postgres/Docker không chạy, nên chưa so được `financial_facts` cũ với dữ liệu mới và chưa ghi thử. Cần chạy `fetch_financials.py --ticker HPG --dry-run` rồi so với bảng hiện có trước khi chạy job Dagster.

## Chưa thay (vẫn dùng vnstock vendored)
- `ingest/fetch_dividends.py` và scraper sự kiện doanh nghiệp (`data/corporate_events_scraper.py`): đã grep mã nguồn vnmarket, không có endpoint cổ tức / sự kiện (chỉ có `dividend_yield` của Fireant).

## Kiểm thử
- Test mới: `tests/test_vnmarket_tools.py` (10), `tests/test_fetch_prices_vnmarket.py` (2); `tests/test_fireant_ingest.py` 49 pass.
- Kiểm tra live: HPG foreign flow 2026-10-09..13 khớp bảng `foreign_flows` trong Postgres; `listed_shares` HPG 8,442,964,520; SJC 1L mua 141.3 / bán 144.3.
- `tests/test_minio_pipeline.py` và `tests/test_pipeline.py::test_new_pdf_indexed` lỗi do MinIO; test router/memory/e2e dùng LLM thật lỗi do DeepSeek `402 Insufficient Balance` (xem BLOCKED.md).

Repro: `pytest tests -m "not live" --ignore=tests/test_fetch_live.py`
