# Xử lý báo cáo test agent (10/10/2026)

Nguồn: `vn_stock_agent_test_report.md`. Trạng thái: **code đã sửa, chưa chạy** (theo quy tắc dự án, user tự chạy pytest và agent thật).

## Ánh xạ lỗi → sửa

| # | Lỗi | Sửa |
|---|-----|-----|
| 1 | Câu hỏi toàn thị trường route sang market_brief | intent mới `market_data` (`agents/intents/market_data.py`) + `agents/intent_rules.py` |
| 2 | Khối lượng đột biến chỉ ra mã A* | `screening.py`: quét bulk `ohlcv_daily`, xếp hạng theo chỉ tiêu, hiển thị "x lần TB20", loại mã KLGD TB < 10,000 |
| 3 | Câu P/E "cao hơn/thấp hơn" ngược | `fundamentals._rank_text` so sánh theo giá trị |
| 4 | "TB ngành" thực ra là TB nhóm so sánh | nhãn `TB nhóm so sánh` khi so sánh mã |
| 5–7 | Thiếu nhận định/disclaimer, rò ngữ cảnh | `agents/guardrails.py` (cờ rủi ro, disclaimer cố định gắn bằng Python) + quy tắc prompt "chỉ trả lời câu hiện tại" |
| 8 | Viết thơ | `is_off_topic` chặn trước LLM + mô tả tool/classifier |
| 9 | Lộ "KHÔNG CÓ TEMPLATE" | thông điệp sạch + `scrub_internal` |
| 10 | Lộ `financial_facts/stock_prices`, mặc định HPG | bỏ `ticker or "HPG"`, không echo `route.reason`, `rag_extras.py` cho cổ tức/ROE/EPS năm |
| 11 | RSI trực tiếp không có dữ liệu | luật chỉ báo → `technical_analysis` |
| 12 | Doanh thu quý | ghi chú giới hạn dữ liệu (xem BLOCKED.md) |
| 13 | Liệt kê ngành | topic `list` của `market_data` |
| 14 | Hỏi tiếng Anh trả tiếng Việt | `detect_language` + quy tắc prompt |
| 15 | Vàng SJC bị từ chối | luật commodity → `macro_sector` (đã có `get_vn_gold`) |

Mục B: bảng N phiên + vốn hóa + ngày dữ liệu trong `price_action`. Mục C: tiêu đề bản tin ngày nghỉ (`market_brief_graph.render_report`), alias ngành ("bank", "thép con", không dấu) trong `agents/focus.py`.

## Cách kiểm tra
```
pytest tests/test_report_fixes.py
```
Sau đó chạy lại các câu trong báo cáo trên agent thật và đối chiếu từng mục.

## Vòng 2 — 8 câu chưa trả lời được (`vn_stock_agent_test_report.md`)

Trạng thái trước khi sửa (đã query DB): `securities.listed_shares` = 0 ở 431/431 mã đang hoạt động; `financial_facts` của PNJ chỉ có kỳ 2020–2025 (năm). Giá HPG 20.1, PNJ 19.5 và P/E PNJ 3.53 (= 19,500 ÷ EPS 5,517) **khớp nhau trong DB** — chưa đối chiếu được với nguồn ngoài.

| # | Câu hỏi | Thay đổi | Dữ liệu (Dagster tự nạp) |
|---|---|---|---|
| 1 | Vốn hóa VNM | `rag_extras._market_cap` + luật `marketcap→rag_qa` (`intent_rules.py`) | `python ingest/fetch_shares.py` |
| 2 | Top 5 vốn hóa HOSE | `market_data._marketcap` (+ ghi chú số mã thiếu CP); chỉ tiêu lọc `market_cap` trong `screening.py`, classifier, conversation_router | như trên |
| 3 | Doanh thu/LN quý gần nhất | `fetch_financials.py --quarterly` (period `Q3/2025`, KBS), `rag_extras._quarterly` kèm YoY | `python ingest/fetch_financials.py --ticker all --quarterly --source KBS --period-from 2023 --period-to 2026` |
| 4 | Cổ tức / yield | bảng `dividends` (migration 038), `ingest/fetch_dividends.py`, `rag_extras._dividends_from_table` (yield 12 tháng = cổ tức tiền ÷ giá đóng cửa) | `python ingest/fetch_dividends.py --tickers VNM --show-schema` rồi `--dry-run`, rồi chạy thật |
| 5–6 | ROE/EPS theo năm | `fetch_ratios.py --history` ghi `ratio_*` vào `financial_facts`; `rag_extras._yearly` ưu tiên số công bố, fallback ước tính | `python ingest/fetch_ratios.py --history` |
| 7 | Tỷ giá trung tâm | thêm nguồn mirror có kiểm tra ngày (`fx_scraper._fetch_central_rate_mirror`) | xem BLOCKED.md — chưa có nguồn khả dụng |
| 8 | Vàng SJC so với hôm qua | bảng `daily_snapshots`, `data/snapshots.py`, `ingest/fetch_daily_snapshots.py`; `get_vn_gold`/`get_fx_rates` ghi snapshot và in chênh lệch | `python ingest/fetch_daily_snapshots.py` mỗi ngày (đã gắn vào `daily_ingest.py`) |

Lỗi khác: `SECTOR_OVERRIDES` trong `data/hose_universe.py` (VRE→Bất động sản, EVF bỏ nhãn "ngân hàng", PNJ→Hàng tiêu dùng, HDG→Tiện ích); khối ngoại tách giao dịch thỏa thuận khỏi xếp hạng; nhãn ngày thống nhất `phiên giao dịch gần nhất (Thứ … dd/mm/yyyy)`. Ngưỡng thanh khoản cho top tăng/giảm (KLGD ≥ 100,000) đã có sẵn từ trước.

Nạp dữ liệu: không có bước thủ công. `pipeline/assets_market_data.py` định nghĩa 5 asset (`securities_shares` CN 02:00, `stock_dividends` T2–T6 19:00 + CN 03:00 toàn bộ mã, `ratio_history` CN 04:00, `financials_quarterly` CN 05:00, `daily_snapshots` 09:00 và 18:00 hằng ngày), và `data_bootstrap_sensor` (mỗi 10 phút) tự khởi chạy job cho bảng còn rỗng, mỗi bảng tối đa một lần/ngày. Migration 038 đã chạy ngày 10/10/2026. Kiểm tra: `pytest tests/test_data_gaps.py` (33 test, không cần mạng/DB).

## Chạy thật và sửa vòng 2 (11/10/2026)

Chạy thật qua graph (LLM + tool, không mock) 4 câu của báo cáo vòng 2; kết quả đọc từ output:

| Câu hỏi | Kết quả |
|---|---|
| Vốn hóa VNM / HPG / BCG | 120.590 / 169.704 / 2.227 tỷ (số CP niêm yết × giá đóng cửa 09/10/2026) |
| Cổ tức HPG, tỷ suất cổ tức VNM | HPG: 6 đợt (cổ phiếu 10–30%, tiền mặt 500 đ), tỷ suất 2,49%; VNM: 3,81% (2.200 đ/cp ÷ 57.700 đ) |
| Tỷ giá trung tâm | 25.629 đ, ngày 10/10/2026 (trang chủ SBV); so với phiên 09/10 +0 |
| Vàng SJC so với hôm qua | có bảng 11/10 vs 10/10, chênh +0,00% (giá hai ngày giống nhau) |

Số liệu DB đã đo: `securities` 430/430 mã active có `listed_shares` (trước: 428/431); `stock_ratios` 430/431 mã làm mới (`FC1` không có dữ liệu → bỏ khỏi `HOSE_SEED`, `is_active=false`); P/E×EPS lệch giá >5%: 348/409 → 0 sau khi xử lý BCG/HID/TCO.

Thay đổi: `rag/sql_agent.py` (bảng `dividends`, mã quý), `ingest/fetch_ratios.py` (`_reprice` dự phòng), `ingest/fetch_shares.py` (cột trùng của VCI), `data/fx_scraper.py` + `tools/global_market.py` (SBV + so với phiên trước), `rag/qa.py` + `agents/graph.py` (không lộ lỗi kỹ thuật), `agents/intents/fundamentals.py` (fallback re-price + ngày cập nhật), `agents/intents/market_data.py` (đơn vị thanh khoản), `agents/run.py`.

Tái hiện: `python -m ingest.fetch_ratios` (làm mới tỷ số), `python ingest/fetch_shares.py` (số CP).

## Giá hôm nay trả phiên cũ (12/10/2026)

**Triệu chứng:** hỏi "giá của HPG bây giờ" lúc 12:04 thứ Hai được `20,100` (đóng cửa 09/10, "dữ liệu tính đến phiên 2026-10-09") kèm dòng `20,450` — hai nguồn lệch nhau.

**Đã xác minh (probe lúc 12:04):** Fireant và VCI đều trả nến 12/10 (close 20,450); KBS trả nến 12/10 (close 20,700, trễ nhẹ); TCBS `bars-long-term` 404. Nguyên nhân: `get_historical_ohlcv` đọc DB trước, `_is_db_fresh` coi DB phiên thứ Sáu là fresh cả ngày thứ Hai (DB nạp 18:00) nên không bao giờ gọi API live.

**Thay đổi:** `tools/price.py` (`_is_db_fresh`: sau 09:00 ngày giao dịch, DB phải có nến hôm nay; nhãn `get_realtime_price` theo giờ), `tools/providers.py` (TTL cache 60s trong 09:00–15:30, fallback TCBS → KBS), `tools/cache.py` (TTL OHLCV theo giờ), `agents/intents/price_action.py` (một nguồn giá + nhãn "đang giao dịch"), `agents/intents/fundamentals.py` (ghi rõ P/E, P/B theo giá tại ngày cập nhật), `agents/conversation_router.py` (khối ngoại một mã → `price_action`).

**Kết quả thật (DeepSeek):** "giá của HPG bây giờ" → `price_action`/HPG → "Giá HPG đang giao dịch: 20,450 VND (tăng 1.74%), dữ liệu tính đến phiên 2026-10-12" (lần sau trong ngày: 20,750, +3.23%, giá live thay đổi đúng).

Tái hiện: `python -c "from agents.intents.price_action import gather_data; print(gather_data('HPG','giá'))"`.

