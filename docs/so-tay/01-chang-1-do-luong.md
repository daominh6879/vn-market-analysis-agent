# CHẶNG 1 · Có cân trước khi giảm cân (tuần 0–1)

> Không viết một dòng RAG nào cho tới khi đo được. Năm bài này là lý do 37 bài sau có ý nghĩa.

---

### Bài 1 · Dựng sân chơi (Đã hoàn thành) 🔴
**~1 ngày**

**Bối cảnh.** Nền móng mà mọi bài sau đứng lên. Không có 4 container này — không có chỗ lưu vector, không có chỗ lưu trạng thái agent, không có chỗ chứa file gốc. Bài 1 không dạy RAG; nó dạy bạn cái khung chứa RAG và thói quen cấu hình-fail-fast mà 41 bài sau đều dựa vào.

**Để hiểu gì.** Các thành phần của một hệ thống AI ghép vào nhau thế nào, và vì sao mỗi cái tồn tại — trước khi dùng đến chúng.

**Làm gì.** *(Tham khảo — đã hoàn thành)*
- `docker-compose.yml` với 4 dịch vụ: **Qdrant**, **Postgres**, **Redis**, **MinIO**.
- Cây thư mục: `api/ agents/ tools/ rag/ data/ llm/ memory/ core/ infra/ evals/ tests/`
- `core/config.py` dùng `pydantic-settings`, đọc `.env`, crash ngay khi thiếu biến.
- `core/logging.py` dùng `structlog` xuất JSON có `trace_id`.
- `Makefile` với `make up`, `make down`, `make test`.

**Xong khi.**
- [x] `make up` → 4 container healthy, mở được Qdrant dashboard và MinIO console
- [x] Xoá 1 biến trong `.env` → crash kèm thông báo rõ thiếu biến nào

**Tự trả lời được.**
- Vì sao cần cả Qdrant *và* Postgres? *(Bài 12 trả lời sâu, nhưng nên đoán được từ đây.)*
- Vì sao nên crash lúc khởi động thay vì lúc có request đầu tiên?

**Cái bẫy.** Dùng `uv` hoặc `poetry` với lock file, không `pip install` trực tiếp. Bạn sẽ dựng lại môi trường này vài lần.

---

### Bài 2 · Lớp trung gian gọi model (Đã hoàn thành) 🔴
**~1.5 ngày**

**Bối cảnh.** Bạn sẽ gọi model ở ít nhất 15 bài sau. Bài này xây một điểm duy nhất để làm việc đó, thay vì rải lệnh gọi API khắp nơi. Khi muốn đổi provider hoặc thêm logging/retry, bạn chỉ sửa một file — không sửa 30 chỗ.

**Để hiểu gì.** Khác biệt giữa "gọi API" và "thiết kế một biên giới". Bạn sẽ thấy bằng mắt: hai model trả về cùng một thứ theo hai định dạng khác nhau như thế nào.

**Làm gì.** *(Tham khảo — đã hoàn thành)*
- `llm/base.py`: interface với `generate()` và `stream()`.
- `llm/types.py`: `LLMResponse` chuẩn hoá — `text`, `tool_calls`, token vào/ra, version model, lý do dừng, thời gian.
- 5 provider clients, bao gồm Ollama local và các API key Anthropic/OpenAI/Gemini.
- `llm/factory.py`: đọc biến môi trường, trả về client tương ứng.
- Gom lỗi provider về exception của bạn: quá tải · bị chặn nội dung · lỗi nguồn.

**Xong khi.**
- [x] Cùng một script, đổi `LLM_PROVIDER` giữa hai provider, chạy được cả hai, không sửa dòng code nào
- [x] Test với một fake client, không gọi mạng

**Tự trả lời được.**
- Hai provider trả `tool_calls` khác nhau ở điểm nào? Bạn đã phải chuẩn hoá gì?
- Nếu provider đổi định dạng response, bạn phải sửa mấy file?
- Vì sao có Ollama local lại quan trọng cho bài 4 và bài 38?

**Cái bẫy.** Ollama trên máy yếu rất chậm, nhưng bạn cần nó để chạy bộ đo nhiều lần mà không tốn tiền. Dùng model 3B–8B đã nén.

---

### Bài 3 · Tự tay viết bộ câu hỏi chuẩn (Đã hoàn thành) 🔴
**~2 ngày**

**Bối cảnh.** Đây là "thước kẻ" của cả 42 bài. Từ bài 4 trở đi, mọi quyết định kỹ thuật đều cần một câu hỏi: "cái này có cải thiện không?" — và 25 câu trong bài này là cách duy nhất để trả lời bằng số. Viết tay (không nhờ model) vì bài thật sự dạy bạn hiểu dữ liệu tài chính.

**Để hiểu gì.** Bài duy nhất trong 42 bài mà giá trị nằm ở **dữ liệu**, không ở code. Sau bài này bạn hiểu báo cáo tài chính Việt Nam thật sự trông như thế nào — thứ khiến 15 bài sau bớt mù mờ.

**Làm gì.** *(Tham khảo — đã hoàn thành)*
- File PDF HPG tại `evals/docs/HGP/`, golden set tại `evals/golden_hpg.yaml` (25 câu).
- 25 câu chia 6 nhóm: tra số trong bảng (8) · diễn giải văn bản (5) · so sánh nhiều kỳ (4) · cần hai nguồn (3) · không có đáp án (3) · ngoài phạm vi (2).
- Mỗi câu có: câu hỏi, đáp án, trang nguồn, nhóm.

**Xong khi.**
- [x] 25 câu, mỗi câu tự trả lời được bằng cách chỉ vào trang cụ thể trong PDF
- [x] Đủ 6 nhóm

**Tự trả lời được.**
- Ba điều bạn học được về báo cáo tài chính mà trước đó không biết?
- Vì sao 8 câu tra số là nhóm khó nhất với tìm kiếm ngữ nghĩa? *(Bài 14 sẽ cho bạn số.)*
- Vì sao cần cả câu "không có đáp án trong tài liệu"?

**Cái bẫy.** Bạn sẽ rất muốn nhờ model sinh 100 câu trong 5 phút. Đừng. Model sinh câu tra từ điển tầm thường, và bạn mất luôn cơ hội hiểu dữ liệu — đó chính là thứ bài này tồn tại để dạy. Từ tuần 5, khi đã hiểu dữ liệu, mới dùng model mở rộng lên 80 câu, vẫn xem lại từng câu.

---

### Bài 4 · Bộ đo chạy được và fail được (Đã hoàn thành) 🔴
**~1.5 ngày**

**Bối cảnh.** Tương đương unit test nhưng cho hành vi AI. Hệ thống truyền thống dùng unit test để phát hiện regression; hệ thống AI dùng bộ đo eval. Không có bài này, mọi thay đổi sau đều là đoán mò — bạn không có cách nào biết nó giúp hay hại.

**Để hiểu gì.** Cách biến "tôi thấy nó trả lời hay hơn" thành một con số và một mã lỗi.

**Làm gì.** *(Tham khảo — đã hoàn thành)*
- `evals/run.py`: đọc `golden_hpg.yaml` → chạy pipeline → tính điểm → ghi `evals/baseline.json` + in bảng markdown.
- 4 chỉ số RAGAS: context recall · context precision · faithfulness · answer relevancy. Model chấm điểm chạy bằng Ollama (dùng `--skip-ragas` trong CI vì chậm).
- So với `baseline.json`, thoát với mã lỗi nếu tụt quá ngưỡng. `make eval` đưa vào CI.
- Baseline hiện tại: `refusal_pass_rate = 0.80` (đã ghi `NOTES.md`).

**Xong khi.**
- [x] `make eval` in ra bảng 4 chỉ số
- [x] Cố tình làm hỏng prompt → CI đỏ
- [x] Kết quả baseline "model trần" đã ghi vào `NOTES.md`

**Tự trả lời được.**
- Vì sao "một bộ đo không fail được là bộ đo vô dụng"?
- Bốn chỉ số trên trỏ tới bốn chỗ sửa khác nhau như thế nào?

**Cái bẫy.** Bộ đo cần model để tính điểm nên nó chậm và có nhiễu. Chạy trên 25 câu, đừng chạy 500, và cache vector.

---

### Bài 5 · Đo nhiễu của chính cái cân 🔴
**~0.5 ngày**

**Bối cảnh.** Bộ đo ở bài 4 dùng model để chấm điểm — model có nhiễu ngẫu nhiên ngay cả khi `temperature=0`. Nếu không đo nhiễu đó trước, bạn sẽ tối ưu những dao động tự nhiên và tưởng mình tiến bộ. Bài này xác định "cải thiện thật tối thiểu phải là bao nhiêu" cho toàn bộ 37 bài còn lại.

**Để hiểu gì.** Điều ít người biết: **mọi phép đo đều có nhiễu.** Không biết mức nhiễu thì bạn sẽ tối ưu những con số ngẫu nhiên và tưởng mình đang tiến bộ.

**Làm gì.**

**Phần 1 — Bắt đầu từ đâu:**

1. Kiểm tra `make eval` còn chạy được không — kết quả đầu tiên là lần chạy số 1.
   ```
   make eval 2>&1 | tee eval_run_1.txt
   ```
2. Chạy thêm 4 lần nữa, không thay đổi gì, lưu từng kết quả:
   ```
   make eval 2>&1 | tee eval_run_2.txt
   make eval 2>&1 | tee eval_run_3.txt
   make eval 2>&1 | tee eval_run_4.txt
   make eval 2>&1 | tee eval_run_5.txt
   ```
3. Mở `NOTES.md` và tạo section mới tên **"Ngưỡng nhiễu"** — bạn sẽ điền số vào đây.

**Phần 2 — Chi tiết từng việc:**

- **Thu thập 5 lần đo.** Đọc 4 chỉ số từ mỗi lần chạy: `context_recall`, `context_precision`, `faithfulness`, `answer_relevancy`. Nếu bạn dùng `--skip-ragas`, đo `refusal_pass_rate` và bất kỳ chỉ số nào eval script đang tính. Điều quan trọng là đo chỉ số *nào bạn dùng để so sánh CI*, không nhất thiết phải đủ 4 RAGAS.

  Ghi vào bảng trong `NOTES.md`:
  ```markdown
  ## Ngưỡng nhiễu

  | Lần chạy | refusal_pass_rate | context_recall | faithfulness |
  |----------|-------------------|----------------|--------------|
  | 1        | 0.80              | ...            | ...          |
  | 2        | 0.82              | ...            | ...          |
  | 3        | 0.79              | ...            | ...          |
  | 4        | 0.81              | ...            | ...          |
  | 5        | 0.80              | ...            | ...          |
  | **std**  | **0.011**         | ...            | ...          |
  | **2×std**| **0.022**         | ...            | ...          |
  ```

- **Tính độ lệch chuẩn.** Tính nhanh bằng Python — không cần file riêng, chạy inline:
  ```python
  import statistics
  runs = [0.80, 0.82, 0.79, 0.81, 0.80]  # thay bằng số thật
  std = statistics.stdev(runs)
  print(f"std = {std:.4f}, ngưỡng fail = {2*std:.4f}")
  ```

- **Cập nhật ngưỡng CI.** Mở `evals/run.py`, tìm chỗ so sánh với `baseline.json`, sửa margin từ giá trị bạn đoán trước đây sang `2 × std` vừa tính được. Ví dụ nếu std = 0.011 thì ngưỡng chấp nhận dao động là ±0.022 — thấp hơn mức này mới thật sự là tụt:
  ```python
  NOISE_THRESHOLD = 0.022  # 2 × std đo được từ 5 lần chạy
  
  if baseline_score - current_score > NOISE_THRESHOLD:
      print(f"FAIL: tụt {baseline_score - current_score:.3f}, vượt ngưỡng nhiễu {NOISE_THRESHOLD}")
      sys.exit(1)
  ```

- **Xác nhận CI phản ứng đúng.** Làm hỏng nhẹ prompt (thay đổi đủ để tụt hơn 2×std) → `make eval` phải đỏ. Khôi phục prompt → `make eval` phải xanh.

**Xong khi.**
- [ ] Bảng 5 lần chạy trong `NOTES.md` dưới mục **"Ngưỡng nhiễu"**
- [ ] Ngưỡng fail của CI đặt **dựa trên số này**, không phải số bạn đoán

**Tự trả lời được.**
- Độ dao động tự nhiên là bao nhiêu? **Cải thiện bao nhiêu điểm thì mới là cải thiện thật?**
- Vì sao có nhiễu dù đã đặt `temperature = 0`?

**Cái bẫy.** Bốn tiếng ở đây tiết kiệm hàng chục giờ về sau. Rất dễ bỏ qua vì nó "không tạo ra tính năng nào".
