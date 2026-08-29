Dưới đây là sơ đồ chi tiết các lớp dữ liệu, logic xử lý và nguồn cấp tương ứng mà Agent cần thực thi để giải quyết từng nhóm câu hỏi.

**1. Hành động giá & Dòng tiền (Market Data & Price Action)**

* **Thông tin cần thu thập:** Giá hiện tại, % tăng/giảm, khối lượng giao dịch hiện tại so với trung bình 10-20 phiên (Volume MA), giá trị mua/bán ròng của Khối ngoại và Tự doanh, tỷ lệ bước giá khớp lệnh chủ động (Active Buy vs Active Sell).
* **Cách Agent phân tích (Logic):**
* *Xác định đột biến:* Nếu giá tăng/giảm > 3% và khối lượng vượt > 150% trung bình 20 phiên, Agent kết luận đây là phiên có dòng tiền lớn can thiệp (Breakout hoặc Sell-off).
* *Đo lường phe áp đảo:* Tính toán tổng khối lượng khớp lệnh Mua chủ động so với Bán chủ động. Nếu Mua chủ động chiếm > 60%, lực cầu đang kiểm soát.


**2. Phân tích Kỹ thuật (Technical Analysis)**

* **Thông tin cần thu thập:** Chuỗi giá OHLCV (Mở-Cao-Thấp-Đóng-Khối lượng) tối thiểu 6 tháng. Các chỉ số: RSI(14), MACD, MA20/MA50/MA200, các mốc Pivot (Hỗ trợ/Kháng cự).
* **Cách Agent phân tích (Logic):**
* *Định vị xu hướng:* Giá nằm trên MA20 và MA50 -> Xu hướng tăng ngắn-trung hạn.
* *Quản trị rủi ro:* Agent dò tìm vùng tích lũy dày nhất hoặc đáy cũ gần nhất làm "Hỗ trợ". Điểm cắt lỗ (Cutloss) tự động thiết lập dưới mốc hỗ trợ này 3-5%.
* *Cảnh báo đảo chiều:* Đối chiếu RSI (nếu > 70 là cảnh báo rủi ro đu đỉnh) và tìm phân kỳ MACD.


* **Nguồn cấp dữ liệu:** Dùng thư viện `TA-Lib` hoặc `Pandas-TA` 

**3. Cơ bản & Định giá (Fundamentals & Valuation)**

* **Thông tin cần thu thập:** P/E, P/B hiện tại và trung bình 5 năm của cổ phiếu; P/E trung bình của ngành. Biên lợi nhuận gộp, Tăng trưởng doanh thu/lợi nhuận (QoQ, YoY), Tỷ lệ Nợ vay/Vốn chủ sở hữu (D/E).
* **Cách Agent phân tích (Logic):**
* *So sánh tương đối:* Agent kiểm tra điều kiện (P/E hiện tại < P/E trung bình 5 năm) VÀ (P/E hiện tại < P/E ngành) -> Kết luận định giá đang rẻ.
* *Chất lượng tăng trưởng:* Nếu lợi nhuận tăng nhưng Biên lợi nhuận gộp giảm, Agent phải cảnh báo doanh nghiệp đang hy sinh giá bán để giành thị phần.
* *Rủi ro tài chính:* Đánh giá D/E; nếu tỷ lệ nợ vay cao trong môi trường lãi suất tăng, Agent sẽ gắn cờ rủi ro (Red flag).


* **Nguồn cấp dữ liệu:** FiinTrade API, WiChart API, phần hệ sinh thái dữ liệu của TCBS/Vietstock.

**4. Vĩ mô & Đặc thù ngành (Macro & Sector Drivers)**

* **Thông tin cần thu thập:** Tỷ giá USD/VND. Giá hàng hóa thế giới ánh xạ theo ngành (VD: Dầu Brent cho PVD; Thép HRC cho HPG; Giá Heo hơi cho DBC; Cước vận tải biển Baltic Dry Index cho HAH).
* **Cách Agent phân tích (Logic):**
* *Crack Spread (Biên lợi nhuận gộp kỳ vọng):* Lấy [Giá bán đầu ra] trừ [Chi phí đầu vào]. (VD: Giá HRC thép thế giới tăng mạnh nhưng Giá quặng sắt giảm -> Agent dự phóng biên lợi nhuận HPG quý tới sẽ phình to).
* *Tác động tỷ giá:* Nếu tỷ giá USD/VND tăng, Agent tự động cộng điểm cho các doanh nghiệp xuất khẩu (VHC, FPT) và trừ điểm các doanh nghiệp nợ vay USD lớn (PC1, HVN).


* **Nguồn cấp dữ liệu:** `yfinance` (tỷ giá, dầu thô), TradingEconomics API (kim loại, nông sản), dữ liệu NHNN (thông qua WiChart).

**5. Tin tức & Tâm lý (News & Sentiment)**

* **Thông tin cần thu thập:** Các tiêu đề báo (title) có chứa mã cổ phiếu trong 3 ngày gần nhất. Số lượng bình luận, lượt nhắc đến (mentions) trên diễn đàn.
* **Cách Agent phân tích (Logic):**
* *Giải thích dị thường:* Khi giá cổ phiếu có hành động bất thường ở mục 1, Agent quét qua text tin tức để tìm lý do (VD: "Bắt giam", "Trúng thầu", "Chia cổ tức").
* *Đo lường cực đoan:* Đưa text của cộng đồng qua mô hình NLP. Nếu 90% bình luận là cực kỳ hưng phấn (Bullish) kèm margin thị trường căng cứng, Agent đưa ra lời khuyên "Cẩn trọng phân phối đỉnh".


* **Nguồn cấp dữ liệu:** RSS Feeds (CafeF, Vietnambiz), FireAnt Scraper; mô hình `PhoBERT` (hoặc prompt LLM) để chấm điểm Sentiment (-1 đến 1).

**6. Tổng hợp & Lọc cổ phiếu (Screening)**

* **Thông tin cần thu thập:** Toàn bộ metrics (Kỹ thuật + Cơ bản) của hơn 1,600 mã trên 3 sàn HOSE, HNX, UPCOM.
* **Cách Agent phân tích (Logic):**
* Đóng vai trò như một bộ lọc SQL tự động. Agent chuyển đổi câu hỏi bằng ngôn ngữ tự nhiên (VD: "Tìm cổ phiếu chứng khoán đang tích lũy") thành Query lọc: `Sector == 'Financials' AND RSI > 40 AND RSI < 60 AND Price > MA50`.


* **Nguồn cấp dữ liệu:** Hệ thống cần tự động snapshot toàn bộ dữ liệu thị trường (End-of-day data) vào Database nội bộ (PostgreSQL) mỗi ngày một lần để Agent có thể thực thi câu lệnh SQL/Pandas query tốc độ cao, thay vì gọi API lẻ từng mã.