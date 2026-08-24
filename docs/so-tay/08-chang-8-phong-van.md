# CHẶNG 8 · Khi cần đi phỏng vấn (tuần 14–15)

> Ba bài này **không dạy bạn thêm gì về kỹ thuật.** Chúng tồn tại để thuyết phục người khác. Nếu mục tiêu là học, bỏ qua chặng này.

---

### Bài 40 · README dẫn bằng số ⚪
**~1.5 ngày**

**Bối cảnh.** Interviewer mở repo trước buổi gặp. Họ đọc 2 phút. Nếu không thấy số thì không thấy gì cả.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Mở `README.md` hiện tại (hoặc tạo mới). Xoá hết phần mô tả chung chung, giữ lại tên project.
2. Chạy lệnh này để lấy số load test từ bài 34 (nếu chưa lưu): `cat results/load_test.json | python -c "import json,sys; d=json.load(sys.stdin); print(d)"` — copy số p95 latency vào README ngay.
3. Paste bảng benchmark từ bài 16+18 vào đầu tiên. Nếu chưa có file, chạy lại `python evals/run.py --output results/bench.json` để có số.

**Chi tiết từng việc:**
- **Thứ tự các section trong README:** một câu mô tả hệ thống làm gì → sơ đồ kiến trúc 1 trang (dùng mermaid hoặc ảnh PNG) → bảng benchmark tìm kiếm 5 dòng (từ bài 16+18) → bảng so 3 kiến trúc (từ bài 26) → bảng tiền trước/sau (từ bài 38) → load test p95 (từ bài 34) → memory footprint (từ bài 30) → cách chạy trong 5 phút.

  Ví dụ bảng benchmark tối thiểu:
  ```markdown
  | Phương pháp | Recall@5 | Latency p95 |
  |-------------|----------|-------------|
  | BM25        | 0.61     | 45ms        |
  | Dense       | 0.74     | 120ms       |
  | Hybrid      | 0.81     | 95ms        |
  ```

- **`make demo`:** thêm target vào `Makefile` để dựng toàn bộ stack + seed 3 PDF mẫu từ `evals/docs/HGP/`. Người clone repo chỉ cần chạy một lệnh:
  ```makefile
  demo:
      docker compose up -d
      sleep 5
      python scripts/seed_demo.py evals/docs/HGP/
  ```
  Kiểm tra bằng cách clone repo sang thư mục khác và chạy thử `make demo` — hệ thống phải lên trong 5 phút.

**Xong khi.** Người khác clone repo, `make demo`, có hệ thống chạy trong 5 phút *(nhờ một người thử thật)*. README có ≥ 5 bảng số.

**Tự trả lời được.** "Con số nào trong README ấn tượng nhất với bạn và tại sao?"

**Cái bẫy.** Đừng liệt kê 20 công nghệ ở đầu README. Liệt kê 5 con số.

---

### Bài 41 · `DECISIONS.md` — 10 quyết định ⚪
**~1 ngày**

**Bối cảnh.** Câu hỏi phỏng vấn kiểu "tại sao chọn X không chọn Y" sẽ xuất hiện. `DECISIONS.md` là câu trả lời chuẩn bị sẵn, đồng thời chứng minh bạn đã thật sự suy nghĩ chứ không chỉ copy tutorial.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Mở `NOTES.md` và `BLOCKED.md`, đọc lại từ đầu. Highlight bất kỳ chỗ nào bạn viết "tôi chọn X vì..." hoặc "thử Y nhưng không ổn vì...".
2. Tạo file `DECISIONS.md` với 10 heading đánh số. Điền tên quyết định trước, nội dung điền sau.
3. Bắt đầu với quyết định dễ nhất — ví dụ chunking strategy — để có đà.

**Chi tiết từng việc:**
- **Format mỗi quyết định** — giữ đúng 5 dòng, không viết dài hơn:
  ```markdown
  ## 3. Chiến lược chunking
  - **Chọn:** sliding window 512 token, overlap 64
  - **Đã cân:** fixed-size 256, semantic chunking
  - **Vì sao:** fixed-size 256 cắt đứt câu → recall giảm 8%; semantic chunking chậm 3× khi index
  - **Đánh đổi:** overlap 64 làm index lớn hơn ~12%, chấp nhận được
  - **Ngày:** 2026-07-15
  ```

- **10 quyết định nên gồm:** công cụ parse PDF · chiến lược chunking · embedding model · cách ghép kết quả hybrid · kiến trúc agent (bài 26) · nơi lưu từng loại memory · chọn model theo việc · một quyết định bạn làm sai rồi sửa · và 2 quyết định khác từ `NOTES.md`. Số lượng không quan trọng bằng việc mỗi cái có phần "đánh đổi" rõ ràng.

- **Mục "Known limitations":** copy từ `BLOCKED.md`, đặt ở cuối file. Thêm ước tính thời gian sửa nếu có.

**Xong khi.** 10 quyết định, mỗi cái có **đánh đổi** rõ ràng. Có ít nhất 1 quyết định ghi lại việc bạn chọn sai và sửa.

**Tự trả lời được.** "Quyết định nào bạn tiếc nhất và giờ sẽ làm khác đi?"

**Cái bẫy.** Quyết định không có phần "đánh đổi" thì chỉ là ghi chép. Phần đánh đổi mới chứng minh bạn đã cân nhắc.

---

### Bài 42 · Video 3 phút + luyện nói ⚪
**~1.5 ngày**

**Bối cảnh.** Nhiều vòng phỏng vấn yêu cầu demo trực tiếp hoặc gửi video trước. 3 phút là đủ — nếu cần hơn, script chưa tốt.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Dựng stack bằng `make demo` (từ bài 40), đảm bảo hệ thống đang chạy và có dữ liệu HPG thật.
2. Mở OBS hoặc bất kỳ screen recorder nào, set resolution 1080p. Chuẩn bị terminal + browser tab trace (Langsmith hoặc tương đương) side-by-side.
3. Viết script 3 phút ra giấy trước: [0:00–0:30] hỏi mã → kế hoạch · [0:30–1:30] 3 bước chạy song song · [1:30–2:00] dừng chờ duyệt → đồng ý · [2:00–3:00] báo cáo + mở trace chỉ vào tiền và thời gian.

**Chi tiết từng việc:**
- **Nội dung video — không giải thích code:** hỏi một mã cổ thật (ví dụ "phân tích HPG Q1 2024") → kế hoạch agent hiện ra → chỉ vào timestamp 3 bước chạy song song → agent dừng chờ duyệt → nhấn đồng ý → báo cáo ra với trích nguồn trang PDF → mở trace chỉ vào cost và latency từng bước. Quay liên tục, không cắt ghép.

- **Luyện nói — bấm giờ từng mục:**
  - Vẽ kiến trúc trên giấy trắng: **3 phút** không nhìn tài liệu
  - Giải thích bảng benchmark: **60 giây** — phải có ít nhất 2 con số
  - Giải thích quyết định kiến trúc agent (bài 26) kèm số: **90 giây**
  - Giải thích lỗi cache sai HPG/HSG (bài 32) và cách sửa: **60 giây**
  - Điểm yếu lớn nhất của hệ thống + kế hoạch sửa: **60 giây**

  Luyện từng mục đến khi đúng giờ mới chuyển mục tiếp theo. Nếu quá giờ thì cắt bớt, không nói nhanh hơn.

**Xong khi.** Video 3 phút không cắt ghép giấu lỗi. 5 câu nói trơn trong thời lượng, **có số trong mỗi câu**. Người ngoài ngành hiểu được hệ thống làm gì trong 30 giây đầu.

**Tự trả lời được.** "Hệ thống của bạn khác gì so với RAG thông thường?" — trả lời trong 60 giây, có số.

**Cái bẫy.** Đừng quay video giải thích code. Quay video **hệ thống đang làm việc**.

---


# Bảng tổng

| Tuần | Bài | Bạn hiểu thêm gì |
|---|---|---|
| 0–1 | 1–5 🔴 | Các thành phần ghép vào nhau thế nào · dữ liệu tài chính thật trông ra sao · **mọi phép đo đều có nhiễu** |
| 2 | 6–7 🔴 | PDF hỏng ở đâu · đánh đổi chunk size · lần đầu ra quyết định bằng số |
| 3 | 8–9 🔴 | Chọn model bằng số của mình · idempotent nghĩa là gì |
| 4 | 10–13 🟡🔴 | Index sẽ lệch khỏi nguồn · file rác lọt vào index · **khi nào nên đổi công cụ thay vì tinh chỉnh** · vì sao cần orchestrator |
| 5 | 14–16b 🔴 | Vì sao cần cả hai loại tìm kiếm · hai thang điểm không cộng được · **hai tầng tìm–chấm** · một truy vấn không đủ — sinh nhiều, gộp bằng RRF |
| 6 | 17–19 🟡🔴 | Cách ly làm trong lúc tìm, không lọc sau · chống bịa số bằng kiến trúc · tool phải đúng trước khi model gọi |
| 7 | 20–22 🔴🟡 | **Hợp đồng lỗi chữa agent lặp vô hạn** · biên giới tool/agent · luồng nhiều bước hoạt động thế nào |
| 8 | 23–24 🔴 | Kế hoạch có schema · **async không tự động là song song** |
| 9 | 25–27 🔴 | Đường thất bại là phần của thiết kế · **chọn kiến trúc theo số liệu** · thiết kế state là thiết kế khả năng khôi phục |
| 10 | 28–30 🔴 | Memory: khi nào ghi, mâu thuẫn, quên · **nhớ sai nguy hiểm hơn không nhớ** |
| 11 | 31–33 🔴 | Streaming cải thiện cái gì · **"chạy được" khác "đúng"** · hệ thống vỡ tử tế thế nào |
| 12 | 34–36 🟡 | Điểm vỡ không phải CPU của bạn · chỗ `tenant_id` thất lạc · cửa chặn bộ đo trong CI |
| 13 | 37–39 🔴🟡 | Quan sát hệ thống có luồng không xác định · **đo trước khi tối ưu** · không đổi gì cũng có thể tệ đi |
| 14–15 | 40–42 ⚪ | *(Không dạy thêm kỹ thuật — chỉ để thuyết phục người khác)* |

---

# Bảy bài giá trị học cao nhất

Nếu chỉ làm 7 bài, làm 7 bài này:

1. **Bài 5** — đo nhiễu của phép đo. Đổi cách bạn nhìn mọi con số về sau.
2. **Bài 12** — tách hai đường dữ liệu. Bài học về **thiết kế**, không về công cụ.
3. **Bài 20** — hợp đồng lỗi của tool. Phản trực giác nhất, hữu dụng nhất.
4. **Bài 24** — chạy song song. Dạy `asyncio` thật sự hoạt động thế nào.
5. **Bài 26** — so 3 kiến trúc. Dạy cách ra quyết định và tính trung thực trí tuệ.
6. **Bài 30** — đo memory. Dạy rằng nhớ sai nguy hiểm hơn không nhớ.
7. **Bài 32** — tự tạo ra một kết quả cache sai. Dạy khoảng cách giữa "chạy được" và "đúng".

**Bốn trong bảy bài dạy bạn điều gì đó không phải về AI** — và chúng sẽ còn đúng khi LangGraph, Qdrant đã bị thay bằng thứ khác.

> **Bài 16b** (RAG-Fusion) không nằm trong danh sách bảy bài trên vì nó dạy *một kỹ thuật* hơn là *một nguyên tắc*. Nhưng nếu project hướng đến phân tích đa nguồn (tin tức + số liệu + báo cáo), đây là bài bắt buộc.
