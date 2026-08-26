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
