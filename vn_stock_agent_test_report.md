# Các câu agent chưa trả lời được và nguồn dữ liệu gợi ý để implement

Ghi chú: đây là các câu agent trả "không có dữ liệu / chưa hỗ trợ" ở lần test cuối. Các nguồn bên dưới là gợi ý để bạn đánh giá; tôi chưa kiểm tra giới hạn gọi API, điều khoản sử dụng, độ trễ hay chất lượng dữ liệu của từng nguồn.

## 1. Danh sách câu chưa trả lời được

| # | Câu hỏi test | Hiện trạng | Dữ liệu còn thiếu |
|---|---|---|---|
| 1 | Vốn hóa của VNM là bao nhiêu? | Thiếu số lượng cổ phiếu để tính | Số cổ phiếu lưu hành (hoặc vốn hóa có sẵn) theo mã |
| 2 | Top 5 cổ phiếu vốn hóa lớn nhất sàn HOSE | "Chưa hỗ trợ tiêu chí lọc/xếp hạng này" | Vốn hóa toàn thị trường để xếp hạng, thêm tiêu chí `market_cap` vào bộ lọc |
| 3 | Doanh thu và lợi nhuận quý gần nhất của FPT | Chỉ có kỳ năm | Báo cáo tài chính theo quý (doanh thu thuần, LNST, ...) |
| 4 | Cổ tức của HPG năm nay? / lịch sử cổ tức VNM / tỷ suất cổ tức | Không có dữ liệu cổ tức/quyền | Lịch sử chia cổ tức (tiền, cổ phiếu), ngày GDKHQ, dividend yield |
| 5 | ROE HPG năm 2020 và 2024 (theo từng năm) | Chỉ có ROE năm gần nhất | Chuỗi chỉ số tài chính nhiều năm/quý |
| 6 | EPS VNM năm 2023 | Không có số liệu theo năm cụ thể | Lịch sử EPS theo năm/quý |
| 7 | Tỷ giá trung tâm USD/VND (SBV) | Chỉ có tỷ giá niêm yết Vietcombank | Tỷ giá trung tâm do NHNN công bố |
| 8 | Giá vàng SJC biến động so với hôm qua | Chỉ có giá hôm nay | Lịch sử giá vàng SJC theo ngày |

Các lỗi khác cần xử lý bên ngoài việc thêm dữ liệu:
- Nhóm "ngành bán lẻ" đang lẫn PNJ (trang sức) và VRE (BĐS); HDG xếp vào bất động sản; danh sách ngân hàng có EVF (công ty tài chính). Cần rà bảng phân ngành.
- Số liệu nghi vấn cần đối chiếu nguồn thật: giá HPG 20,100, PNJ 19,500 và P/E 3.5, P/E CTG 4.7, SHB 3.5.
- Ngày dữ liệu: 10/10/2026 là thứ Bảy nhưng có chỗ ghi "phiên 10-10", chỗ ghi "10-09". Nên thống nhất "phiên giao dịch gần nhất".
- Top tăng giá/khối ngoại: nên có ngưỡng thanh khoản tối thiểu (SHA tăng 6.96% chỉ với 10,300 cp) và tách giao dịch thỏa thuận (MCH -9,593 tỷ).

## 2. Nguồn dữ liệu gợi ý

| Cần | Nguồn gợi ý | Ghi chú |
|---|---|---|
| Vốn hóa, số cổ phiếu lưu hành, báo cáo tài chính quý/năm, chỉ số tài chính nhiều kỳ, cổ tức, giá lịch sử | Thư viện mã nguồn mở **vnstock** ([GitHub vnstock-official/vnstock](https://github.com/vnstock-official/vnstock), [bản mô tả trên PyPI/Tessl](https://tessl.io/registry/tessl/pypi-vnstock)) | Gói Python lấy dữ liệu từ các nhà cung cấp trong nước (như VCI, TCBS). Cần kiểm tra phiên bản hiện hành, hạn mức và điều khoản của gói và của từng nguồn phía sau. |
| Tỷ giá trung tâm USD/VND | Trang thông tin tỷ giá của **Ngân hàng Nhà nước** ([sbv.gov.vn, mục Tỷ giá hối đoái](https://sbv.gov.vn/documents/d/sbv_portal/580116)) | Nguồn chính thức, cần viết bộ thu thập vì chưa chắc có API. |
| Cổ tức, sự kiện quyền | Cổng công bố thông tin của **HOSE/HNX** và các trang tin (CafeF, Vietstock); hoặc vnstock nếu có endpoint sự kiện | Cần xác minh trường dữ liệu trước khi dùng. |
| Giá vàng SJC lịch sử | Lưu snapshot hằng ngày từ nguồn đang dùng cho market brief | Cách rẻ nhất là tự lưu vào DB mỗi ngày để có "hôm qua". |
| Dữ liệu trả phí, ổn định hơn | Các nhà cung cấp dữ liệu thương mại trong nước (ví dụ FiinTrade) | Tôi chưa kiểm tra giá và điều khoản. |

## 3. Gợi ý thứ tự implement

1. Bảng `shares_outstanding` / `market_cap` → mở khóa câu 1 và 2 (thêm `market_cap` vào bộ lọc và template SQL).
2. Bảng `financial_facts` kỳ quý và nhiều năm → câu 3, 5, 6.
3. Bảng `dividends` (ngày, loại, giá trị, GDKHQ) và tính dividend yield → câu 4.
4. Job lưu snapshot hằng ngày cho tỷ giá trung tâm và giá vàng → câu 7, 8.
5. Rà lại phân ngành, ngưỡng thanh khoản và nhãn ngày (mục 1 phần "lỗi khác").
6. Chạy lại bộ câu hỏi test trong báo cáo `vn_stock_agent_test_report.md` để kiểm tra hồi quy.