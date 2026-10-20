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
- Lệnh: `pytest tests/test_portfolio_ledger.py tests/test_portfolio_evidence.py tests/test_portfolio_advisor.py tests/test_portfolio_tcbs.py` → 55 passed (cùng các test breakout/proprietary/backtest).

## Đồng bộ TCBS (đọc, không đặt lệnh)
- Nguồn: TCBS iFlash Open API v1.0.0 (https://developers.tcbs.com.vn/docs/v1.0.0/introduction/). Đăng nhập: `POST /gaia/v1/oauth2/openapi/token` với `apiKey` + `otp` (Smart OTP từ app TCInvest) → JWT Bearer. Tài sản: `GET /aion/v1/accounts/{accountNo}/se` (symbol, quantity, avgPrice, marketValue). Tiền: `GET /aion/v1/accounts/{accountNo}/cashInvestments` (cashBalance).
- Vì cần OTP mỗi lần nên đồng bộ do người dùng kích hoạt, không đưa lên Dagster schedule: `POST /portfolio/{user_id}/tcbs/sync` body `{"otp": "..."}`; `.env` cần `TCBS_API_KEY`, `TCBS_ACCOUNT_NO` (tiểu khoản thường).
- TCBS chỉ trả vị thế + tiền, không có lịch sử giao dịch → sổ cái được "mirror": xoá các dòng `note='tcbs_sync'` cũ, ghi 1 khoản nạp mở đầu + mỗi mã 1 lệnh mua tại `avgPrice` (phí 0), lùi 5 phiên để bán được ngay. Từ chối (400) nếu user đã có giao dịch nhập tay → dùng `user_id` riêng cho TCBS. Mã không có trong `securities` (quỹ, trái phiếu) bị bỏ qua và liệt kê trong `skipped_symbols`.
- Đã kiểm tra với DB thật bằng snapshot giả (chưa gọi TCBS thật vì chưa có API key): đồng bộ 2 mã + tiền 200M → vị thế/tiền đúng, đồng bộ lại không nhân đôi, `XXXFUND` bị bỏ qua, user có giao dịch tay bị từ chối. Phần gọi HTTP chỉ kiểm tra bằng test mock (URL, body token, không lộ API key trong lỗi).
- Cố ý không dùng endpoint đặt/sửa/huỷ lệnh: AI chỉ tư vấn, người dùng tự đặt lệnh.
