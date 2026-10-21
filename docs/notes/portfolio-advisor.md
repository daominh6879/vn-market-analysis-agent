# Portfolio API — AI tư vấn mua/bán + đồng bộ TCBS

Kế hoạch: `docs/plans/portfolio-api.md`. Mọi số liệu dưới đây lấy từ lần chạy thật ngày 2026-10-19 (Postgres + DeepSeek thật); user thử đã xoá sau khi test.

## Kiến trúc
- `portfolio/ledger.py` — sổ giao dịch, vị thế, T+2, lô 100 (Phase 1, không đổi).
- `portfolio/evidence.py` — gom bằng chứng, không gọi LLM: kỹ thuật (`technical.gather_data`), khối ngoại 5/20 phiên, tin `news_articles` (14 ngày, tối đa 8 bài/mã, mỗi bài có id), bối cảnh thị trường, rủi ro danh mục, tối đa 5 mã ứng viên lọc sẵn (thanh khoản ≥ 5 tỷ, tăng 0–25%/20 phiên, khối ngoại mua ròng 5 phiên, ưu tiên ngành dưới 30% NAV). Chưa dùng `financial_facts`.
- `portfolio/advisor.py` — 1 lần gọi LLM trả JSON verdict từng mã; `parse_verdicts` loại verdict sai enum/thiếu lý do/id tin không thuộc mã; `apply_limits` tự tính khối lượng (bán ≤ số bán được T+2, chốt lời bán 1/2, mua ≤ 10% tiền mặt và trần 25% NAV, lô 100; không mua thêm khi lỗ ≥ 8%). Thiếu verdict → thử lại 1 lần → GIỮ.
- Đã gỡ model LightGBM (xem `portfolio-signals.md`); migration `042_drop_trade_signals.sql` xoá 2 bảng (đã chạy, `to_regclass` trả NULL cho cả hai).

## Kết quả chạy thật `POST /portfolio/{user}/advise`
- Danh mục thử: HPG 5.000 cp (lỗ −31,6%), FPT 1.000 cp (−52,5%), VCB 100 cp mua cùng ngày. HTTP 200 sau ~25 giây, đủ 4 mục + disclaimer, `llm_missing` rỗng.
- FPT → CẮT LỖ 1.000 cp; HPG → GIỮ; VCB → GIỮ (chưa bán được). Mã mới: DGW 400 cp, PVT 800 cp. Mọi link tin trích dẫn có tiêu đề/URL thật từ `news_articles`.
- Vấn đề phát hiện: HPG vượt trần 25% NAV (26,4%) nhưng AI giữ, trong khi phần rủi ro lại nói "cần hạ tỷ trọng HPG". Sửa: prompt đánh dấu mã vượt trần và bắt buộc cân nhắc bán bớt hoặc giải thích. Chạy lại (HPG 28,6% NAV): HPG → CẮT LỖ 5.000 cp, báo cáo nhất quán.
- Hạn chế còn lại: CẮT LỖ bán toàn bộ số bán được, nên khi chỉ vượt trần nhẹ AI phải chọn giữa giữ và bán hết (chưa có hành động "giảm về trần"). AI từng viết "id=..." trong câu chữ; đã thêm lệnh cấm trong system prompt, chưa chạy lại để xác nhận.
- 2026-10-21: chạy lại xác nhận lệnh cấm trong prompt **không có tác dụng** (2/2 lần vẫn có `(id=171436, ...)`). Đã sửa bằng code: `_strip_ids` trong `parse_verdicts` xoá id inline khỏi reasons/risks/tóm tắt. Chạy lại 2 lần: 0 id inline. Chi tiết xem `vn-portfolio/docs/notes/phase-a-b.md`.

## Tách sang app riêng vn-portfolio (2026-10-21)
- Sổ giao dịch, TCBS, rủi ro, lệnh chuyển sang repo `../vn-portfolio` (plan: `vn-portfolio/docs/plan.md`). ai-engineer chỉ còn advisor stateless: router ẩn `api/advisor.py`, gồm `POST /advisor/reference|risk|advise`, header `X-Internal-Key` = env `ADVISOR_INTERNAL_KEY` (env trống thì trả 503).
- Router cũ `/portfolio/*` vẫn chạy (cho tới Phase G). Với cùng 4 giao dịch, positions/cash của router cũ và app mới giống hệt nhau.
- Lệnh: `pytest tests/test_portfolio_ledger.py tests/test_portfolio_evidence.py tests/test_portfolio_advisor.py tests/test_portfolio_tcbs.py` → 55 passed (cùng các test breakout/proprietary/backtest).

## Đồng bộ TCBS (đọc, không đặt lệnh)
- Nguồn: TCBS iFlash Open API v1.0.0 (https://developers.tcbs.com.vn/docs/v1.0.0/introduction/). Đăng nhập: `POST /gaia/v1/oauth2/openapi/token` với `apiKey` + `otp` (Smart OTP từ app TCInvest) → JWT Bearer. Tài sản: `GET /aion/v1/accounts/{accountNo}/se` (symbol, quantity, avgPrice, marketValue). Tiền: `GET /aion/v1/accounts/{accountNo}/cashInvestments` (cashBalance).
- Vì cần OTP mỗi lần nên đồng bộ do người dùng kích hoạt, không đưa lên Dagster schedule: `POST /portfolio/{user_id}/tcbs/sync` body `{"otp": "..."}`; `.env` cần `TCBS_API_KEY`, `TCBS_ACCOUNT_NO` (tiểu khoản thường).
- TCBS chỉ trả vị thế + tiền, không có lịch sử giao dịch → sổ cái được "mirror": xoá các dòng `note='tcbs_sync'` cũ, ghi 1 khoản nạp mở đầu + mỗi mã 1 lệnh mua tại `avgPrice` (phí 0), lùi 5 phiên để bán được ngay. Từ chối (400) nếu user đã có giao dịch nhập tay → dùng `user_id` riêng cho TCBS. Mã không có trong `securities` (quỹ, trái phiếu) bị bỏ qua và liệt kê trong `skipped_symbols`.
- Đã kiểm tra với DB thật bằng snapshot giả (chưa gọi TCBS thật vì chưa có API key): đồng bộ 2 mã + tiền 200M → vị thế/tiền đúng, đồng bộ lại không nhân đôi, `XXXFUND` bị bỏ qua, user có giao dịch tay bị từ chối. Phần gọi HTTP chỉ kiểm tra bằng test mock (URL, body token, không lộ API key trong lỗi).
- Cố ý không dùng endpoint đặt/sửa/huỷ lệnh: AI chỉ tư vấn, người dùng tự đặt lệnh.

## Stop/target trong verdict AI (A2, 2026-10-21)
- Verdict BUY_MORE/BUY_NEW có thêm `stop_price`, `target_price`, `levels_basis`. Python kiểm tra: stop phải thấp hơn giá 2–30%, không đạt thì dùng stop mặc định 7% dưới giá (`levels_source=default`, bỏ `levels_basis` vì căn cứ đó mô tả stop của AI đã bị loại); target phải nằm trong (giá, 2×giá], không đạt thì bỏ.
- Run #6 (trước khi thêm intent bên dưới): DCM stop 33.136 / target 34.838 (`ai`); DGW stop AI 45.645 cao hơn giá tham chiếu 45.450 → mặc định 42.268.

## Thêm intent `fundamentals` + `breakout` vào evidence (2026-10-21)
- Trước đó evidence chỉ dùng 2/11 intent (`technical`, `closing_commentary`). Nay mỗi mã đang giữ và mỗi mã ứng viên có thêm `fundamentals.gather_data` (P/E, P/B, ROE, EPS so với cùng ngành, tăng trưởng, D/E) và `breakout.gather_data` (pivot, SL, TP). Đo riêng: fundamentals ~1,6–1,9 giây/mã, breakout 0,2–0,8 giây/mã; chạy song song 4 luồng.
- Run #7 (`t1`, 2026-10-21): HTTP 200 sau 42 giây (trước đây ~25 giây), `llm_missing` rỗng. Cả 4 verdict đều dẫn số định giá/tăng trưởng (VD HPG "P/E 7.6x chiết khấu -56% so với ngành 17.2x"). DCM: target 38.352 = TP1 của tín hiệu breakout. DGW: stop AI 42.600 hợp lệ (run #6 phải dùng mặc định).
- `POST /advisor/ask {user_id, message, conversation_id?}`: 1 lượt agent chat đầy đủ (`run_turn`) cho app vn-portfolio, hội thoại riêng `tenant_id=vn-portfolio`; tiếp tục hội thoại của user khác → 404. Kết quả chạy thật: xem `vn-portfolio/docs/notes/phase-d-e-f.md`.
- Lệnh: `pytest -k "portfolio or advisor or evidence"` → 66 passed.

## Phase G — dọn phần portfolio cũ (2026-10-21)
- Kiểm tra trước khi xoá: `portfolio_transactions` có **0 dòng** (không còn dữ liệu cần import); sổ giao dịch thật nằm ở DB `portfolio` của vn-portfolio (`t1`: 4 giao dịch).
- Đã gỡ: router `api/portfolio.py` + mount trong `api/main.py`, `portfolio/tcbs.py`, `tests/test_portfolio_tcbs.py`, `ledger.get_positions/get_cash/add/list/delete_transaction`, `evidence.gather_evidence(user_id)`, `advisor.advise(user_id)`. Còn giữ trong `ledger.py`: hàm thuần (`replay`, `build_positions`, `sessions_elapsed`, `signed_amount`), `fee_pct`/`SELL_TAX_PCT` (dùng bởi `signals/*`, `portfolio/backtest_rules.py`) và `reference()` cho `/advisor/reference`.
- Migration `043_drop_portfolio_transactions.sql` → bảng đã xoá (`to_regclass` = null). Lưu ý `scripts/migrate.py` chạy lại mọi file SQL mỗi lần (039 tạo lại bảng rỗng, 043 xoá ngay sau) và còn chạy cả seed/MinIO/backfill — muốn chỉ áp schema thì dùng `--skip-securities --skip-minio --skip-market-data --skip-audit`.
- Sau khi khởi động lại API: `GET :8031/portfolio/t1/positions` → 404; vn-portfolio `/portfolio/t1/positions` và `/cash` vẫn đúng (NAV 96.810.500).
- Lệnh: `pytest tests/test_portfolio_ledger.py tests/test_portfolio_advisor.py tests/test_advisor_api.py` → 50 passed; `pytest tests/test_portfolio_evidence.py tests/test_backtest_rules.py tests/test_breakout_*.py` → 17 passed.
