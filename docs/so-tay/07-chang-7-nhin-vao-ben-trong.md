# CHẶNG 7 · Nhìn vào bên trong (tuần 12–13)

---

### Bài 37 · Trace và đi tìm bước đắt nhất 🔴
**~1.5 ngày**

**Bối cảnh.** Agent không có đường thực thi cố định — mỗi request đi một đường khác nhau tuỳ câu hỏi. Không thể suy ra bước nào chậm nhất bằng cách đọc code; cần trace để quan sát từng bước thật sự mất bao lâu và bao nhiêu token.

**Để hiểu gì.** Cách quan sát một hệ thống mà **luồng thực thi không xác định trước** — với agent, đường đi khác nhau mỗi lần nên không thể suy ra từ đọc code.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài LangSmith và cấu hình:
   ```bash
   uv add langsmith
   ```
   Thêm vào `.env`:
   ```
   LANGCHAIN_TRACING_V2=true
   LANGCHAIN_API_KEY=ls__...
   LANGCHAIN_PROJECT=hpg-agent
   ```
2. Kiểm tra tracing đã hoạt động: chạy một request thử và mở LangSmith dashboard.
3. Nếu muốn chạy local không tốn tiền, dùng Phoenix của Arize:
   ```bash
   uv add arize-phoenix openinference-instrumentation-langchain
   docker run -p 6006:6006 arizephoenix/phoenix:latest
   ```

**Chi tiết từng việc:**

- **Gắn nhãn nghiệp vụ vào mọi bước.** LangSmith tự capture span từ LangGraph nhưng các nhãn nghiệp vụ phải gắn tay:
  ```python
  from langsmith import trace

  with trace(
      name="retrieve_chunks",
      metadata={
          "ticker": state["ticker"],
          "plan_id": state["plan_id"],
          "prompt_version": "v2.1",
          "used_cache": state.get("cache_hit", False),
          "replan_count": state.get("replan_count", 0),
      }
  ) as run:
      chunks = retriever.invoke(state["query"])
  ```
  Gắn `tenant_id` và `model_version` ngay ở entry point, trước khi graph chạy.

- **Chạy 50 request và thu số liệu.** Sau khi chạy xong, mở LangSmith và trả lời 5 câu bằng số (không được đoán):
  1. Bước nào chậm nhất (latency p95)?
  2. Bước nào tốn token nhất?
  3. Tiền trung bình và p95 mỗi request?
  4. Bao nhiêu % request có `replan_count > 0`?
  5. Bao nhiêu % request chạm trần số bước?

- **Thêm endpoint phản hồi người dùng** — nhận `run_id` và `score` (1/0), gắn vào trace bằng LangSmith client.

**Xong khi.**
- [ ] 5 câu trên đều có số
- [ ] Lọc được trace theo điều kiện nghiệp vụ, ví dụ *"tất cả request về HPG bị chạm trần số bước tuần này"*

**Tự trả lời được.**
- Bước nào đắt nhất? **Có phải bước bạn đoán trước khi đo không?** *(Thường là không — đó là bài học.)*
- Vì sao lỗi của hệ thống này thường **không phải exception**? Vậy debug bằng gì?

**Cái bẫy.** Việc gắn nhãn quan trọng hơn việc bật tracing. Hàng nghìn bước vô danh không lọc được.

---

### Bài 38 · Chọn model theo việc — cắt tiền bằng số 🔴
**~1 ngày**

**Bối cảnh.** Dùng model mạnh nhất cho mọi bước là lãng phí: phân loại câu hỏi hay trích memory không cần suy luận sâu. Bài này dùng dữ liệu trace từ bài 37 để xác định bước nào có thể dùng model rẻ hơn — rồi đo cụ thể tiết kiệm được bao nhiêu phần trăm chi phí mà chất lượng tụt trong ngưỡng nhiễu.

**Để hiểu gì.** Bài học cuối về tối ưu: **đo trước, rồi mới tối ưu.** Và model rẻ hỏng ở đâu trước tiên.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Mở LangSmith từ bài 37, sort các span theo `total_tokens` giảm dần. Liệt kê 3–5 bước tốn token nhất.
2. Xác định model đang dùng ở mỗi bước:
   ```bash
   grep -r "model_name\|model=" core/ llm/ --include="*.py" -n
   ```
3. Chạy eval baseline trước khi thay đổi gì:
   ```bash
   python evals/run.py --output evals/before_model_routing.json
   ```

**Chi tiết từng việc:**

- **Xác định các bước không cần model mạnh.** Bước thường có thể dùng model rẻ: phân loại câu hỏi, trích xuất memory/entity, chấm điểm relevance chunk, lập kế hoạch đơn giản. Bước giữ model mạnh: tổng hợp câu trả lời cuối, lý luận multi-hop.

- **Cấu hình model theo từng bước qua adapter:**
  ```python
  STEP_MODEL_MAP = {
      "classify":       "claude-haiku-4-5",
      "extract_memory": "claude-haiku-4-5",
      "synthesize":     "claude-sonnet-4-5",  # giữ model mạnh
      "replan":         "claude-sonnet-4-5",
  }
  ```

- **Bảng so sánh ≥ 3 cấu hình:**

  | Cấu hình | Tiền/request | Điểm chất lượng | Latency p95 |
  |---|---|---|---|
  | All claude-sonnet | $X | baseline | Xs |
  | Routing (haiku/sonnet) | $Y | ? | ? |
  | All qwen3:8b local | $0 | ? | ? |

- **Bật cache tiền tố prompt (prefix caching).** Nguyên lý: Anthropic cache theo prefix. Phần bất biến lên đầu, phần thay đổi xuống cuối. Kiểm tra cache hoạt động: nếu thấy `cache_read_input_tokens > 0` trong response usage thì đúng.

  Sai phổ biến: đặt phần thay đổi (giá cổ phiếu hôm nay) lên đầu → prefix không bao giờ match → cache vô dụng.

**Xong khi.**
- [ ] Bảng ≥ 3 cấu hình × (tiền/request, chất lượng, thời gian)
- [ ] Nói được: *"tiền giảm X% với chất lượng tụt Y điểm (trong ngưỡng nhiễu ở bài 5)"*

**Tự trả lời được.**
- Model rẻ hỏng ở đâu **trước tiên** khi hạ quá sâu?
- Vì sao phần bất biến phải để **đầu** prompt để cache hoạt động?

**Cái bẫy.** Nếu bộ phân loại bắt đầu trả nhãn sai định dạng, đó là dấu hiệu đã hạ model quá sâu — tuân thủ định dạng hỏng trước khả năng suy luận.

---

### Bài 39 · Bộ đo tự động mỗi đêm + giả lập model đổi âm thầm 🟡
**~1 ngày**

**Bối cảnh.** Nhà cung cấp model cập nhật model định kỳ mà không thông báo. Hành vi thay đổi, chất lượng có thể tụt — không có exception, không có log lỗi nào. Bài này xây bộ đo tự động chạy mỗi đêm để phát hiện điều đó, và tự giả lập để kiểm chứng bộ đo có thực sự bắt được không.

**Để hiểu gì.** Với hệ thống truyền thống, không đổi code thì hành vi không đổi. Với hệ thống dùng model, **không đổi gì cũng có thể tệ đi.**

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `.github/workflows/nightly-eval.yml`.
2. Xác định ngưỡng cảnh báo từ bài 5 (thường là baseline trừ 5–10%):
   ```python
   # evals/thresholds.py
   THRESHOLDS = {
       "refusal_pass_rate": 0.72,   # baseline 0.80 - buffer 0.08
   }
   ```
3. Chạy thử workflow bằng tay trước khi đợi đến đêm:
   ```bash
   python evals/run.py --output evals/nightly_$(date +%Y%m%d).json
   python evals/check_thresholds.py evals/nightly_$(date +%Y%m%d).json
   ```

**Chi tiết từng việc:**

- **GitHub Action chạy `make eval` mỗi đêm** (22:00 UTC = 05:00 ICT). Script tự commit kết quả vào `evals/history/`. Khi job exit code 1, GitHub Actions tự gửi email thông báo build failed — đó chính là "cảnh báo nổ".

- **Script `evals/check_thresholds.py`** — đọc kết quả, so với THRESHOLDS, `sys.exit(1)` nếu fail.

- **Vẽ đồ thị chỉ số theo ngày** sau khi có ≥ 3 đêm dữ liệu — bằng matplotlib, lưu PNG vào `evals/history/trend.png`.

- **Giả lập model đổi âm thầm** để kiểm chứng bộ đo. Không thông báo cho chính mình trước. Đổi model synthesis xuống model yếu hơn, push lên, đợi đến đêm (hoặc chạy `workflow_dispatch` ngay). Nếu email cảnh báo đến — bộ đo hoạt động đúng.

**Xong khi.**
- [ ] Chạy tự động 3 đêm liên tiếp, có lịch sử
- [ ] Giả lập đổi model → **cảnh báo nổ**

**Tự trả lời được.**
- Vì sao "đo định kỳ là hoạt động vận hành, không phải hoạt động phát triển"?
- Nếu chất lượng tụt mà code không đổi, nghi phạm số một là gì?

**Cái bẫy.** Bộ đo đêm tốn tiền nếu bộ câu hỏi lớn. Dùng model chấm điểm local, cache vector, tách bộ đo nhẹ (mỗi PR) khỏi bộ đo đầy đủ (mỗi đêm).
