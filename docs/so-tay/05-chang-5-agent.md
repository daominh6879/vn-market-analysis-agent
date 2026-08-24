# CHẶNG 5 · Agent (tuần 7–10)

> Chặng cốt lõi. Thứ tự bắt buộc: **chạy được đơn giản trước, đo, rồi mới thêm phức tạp.** Bài 26 dạy nhiều nhất.

---

### Bài 22 · Graph tuần tự — mốc so sánh 🔴
**~1.5 ngày**

**Bối cảnh.** Đây là bản đơn giản nhất của agent đầy đủ — luồng cố định không quyết định gì, chạy thẳng từ đầu đến cuối. Mục đích chính: tạo mốc để so sánh. Mọi kiến trúc phức tạp hơn ở bài 23–26 phải chứng minh bằng số rằng chúng tốt hơn mốc này.

**Để hiểu gì.** Một luồng nhiều bước thật sự hoạt động thế nào, và vì sao cần mốc đơn giản trước khi xây thứ phức tạp.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo thư mục và cài dependency:
   ```
   mkdir agents
   uv add langgraph langchain-anthropic
   ```
2. Tạo `agents/state.py` với `TypedDict` tối giản, xác nhận import không lỗi.
3. Tạo `agents/graph.py` với 4 node trống, compile graph, in sơ đồ ra file PNG.

**Chi tiết từng việc:**

- **`agents/state.py`** — gồm: `ticker`, `summary`, `price_data_path` (đường dẫn file, **không** nhét bảng số vào đây), `tech_signals`, `risk_verdict`, `report`, `history`, `error`, `step_count`. Quy tắc bắt buộc: **dữ liệu lớn lưu ngoài**, state chỉ giữ đường dẫn.

- **`agents/graph.py`** — LangGraph tuần tự 4 node: `collect → analyze_technical → assess_risk → synthesize`. Xuất ảnh sơ đồ: `app.get_graph().draw_mermaid_png(output_file_path="agents/graph.png")`.

- **Node rủi ro** — rule đơn giản: nếu biến động 14 phiên vượt ngưỡng thì set `risk_verdict = "HIGH_VOLATILITY"` và dừng. Không cần model, chỉ cần if/else.

- **Node tổng hợp** — gọi model, yêu cầu output Markdown **có trích nguồn** dạng `[Nguồn: HPG_Q2_2024.pdf, trang 12]`.

- **`agents/run.py`** — entry point: `python -m agents.run FPT`. Sau khi chạy được 5 mã, ghi số vào `NOTES.md` dưới nhãn **"mốc tuần tự"** (chất lượng, p50/p95 latency, tổng token trung bình).

**Xong khi.**
- [ ] `python -m agents.run FPT` → báo cáo hoàn chỉnh có trích nguồn
- [ ] Xuất được ảnh sơ đồ graph
- [ ] Chạy 5 mã không crash; eval end-to-end đã ghi số dưới nhãn **"mốc tuần tự"**

**Tự trả lời được.**
- Nhìn vào sơ đồ graph, bạn thấy điều gì mà đọc code không thấy?
- Vì sao **không** nên nhét bảng dữ liệu giá vào state?

**Cái bẫy.** Nếu bỏ qua cảnh báo về dữ liệu lớn trong state, bài 27 sẽ fail và bạn sẽ mất thời gian tìm nguyên nhân.

---

### Bài 23 · Kế hoạch là **dữ liệu có schema**, không phải văn xuôi 🔴
**~2 ngày**

**Bối cảnh.** Thay vì hardcode thứ tự bước như bài 22, bài này để model tự lập kế hoạch tuỳ câu hỏi. Nhưng kế hoạch ra dạng JSON có schema — code validate được. Không phải "model tự làm mọi thứ" mà là "model lập kế hoạch, code kiểm tra tính hợp lệ, rồi thực thi".

**Để hiểu gì.** "Planning" nghĩa là gì cụ thể trong hệ thống thật. Sức mạnh của việc biến output model thành cấu trúc **kiểm tra được bằng code**.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `agents/planner.py` với `Step` và `Plan` Pydantic models.
2. Viết `validate_plan(plan, registry)` → danh sách lỗi.
3. Test thủ công với một kế hoạch có vòng lặp.

**Chi tiết từng việc:**

- **Schema kế hoạch:**
  ```python
  class Step(BaseModel):
      id: str
      intent: str
      executor: str       # tên tool hoặc agent
      depends_on: list[str]
      expected_output: str

  class Plan(BaseModel):
      steps: list[Step]
      budget_tokens: int
  ```

- **Validator — kiểm tra 5 điều kiện trước khi chạy:** không có vòng lặp phụ thuộc, mọi `depends_on` là id hợp lệ, mọi `executor` có trong registry, tổng bước ≤ max_steps, `budget_tokens` ≤ giới hạn cứng.

- **Retry 1 lần**: khi plan không hợp lệ, gọi model lần hai với thông báo lỗi cụ thể. Nếu lần hai vẫn sai → dùng kế hoạch tuần tự mặc định từ bài 22.

- **In kế hoạch JSON cho 3 câu hỏi khác độ phức tạp** — câu phức hợp phải sinh nhiều bước hơn câu đơn giản.

**Xong khi.**
- [ ] In kế hoạch JSON cho 3 câu hỏi; câu phức hợp sinh nhiều bước hơn
- [ ] Ép model sinh kế hoạch có vòng lặp → validator bắt được, không crash
- [ ] Kế hoạch mặc định hoạt động khi lập kế hoạch fail 2 lần

**Cái bẫy.** Model rất hay sinh bước phụ thuộc vào một bước **không tồn tại**. Đây là lỗi phổ biến nhất và là lý do validator tồn tại.

---

### Bài 24 · Chạy song song — cảm nhận thời gian bị cắt 🔴
**~1.5 ngày**

**Bối cảnh.** Kế hoạch từ bài 23 thường có nhiều bước độc lập nhau. Chạy song song cắt thời gian chờ đáng kể. Bài này đo cụ thể bao nhiêu và phát hiện một bẫy phổ biến: viết `asyncio.gather` không đảm bảo các hàm blocking chạy thật sự song song.

**Để hiểu gì.** Điểm cắt thời gian lớn nhất. Và một bài học quan trọng về Python: **async không tự động nghĩa là song song**.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `agents/executor.py`.
2. Viết `topological_sort(steps)` → danh sách "tầng" (mỗi tầng là bước độc lập nhau).
3. Log timestamp đầu mỗi bước — nếu 2 bước cùng tầng bắt đầu cách nhau < 50ms thì đang thật sự song song.

**Chi tiết từng việc:**

- **`agents/executor.py`** — mỗi tầng chạy `asyncio.gather`. Nếu `run_step` gọi hàm blocking (đọc PDF, tính toán reranker), bọc bằng `asyncio.run_in_executor(None, blocking_fn, args)` — không thì gather vẫn chạy tuần tự.

- **Log timestamp** — thêm log ở đầu và cuối mỗi bước, nhìn vào log: nếu 3 bước cùng tầng có timestamp START cách nhau đều đặn đúng bằng thời lượng từng bước → đang chạy tuần tự trong áo async.

- **Đo và so sánh** trên 10 câu từ `evals/golden_hpg.yaml`:

  | | p50 (s) | p95 (s) | Tổng token TB |
  |---|---|---|---|
  | Tuần tự (bài 22) | ? | ? | ? |
  | Song song (bài 24) | ? | ? | ? |

**Xong khi.**
- [ ] Bảng: tuần tự vs song song × (p50, p95, tổng token)
- [ ] Log chứng minh 3 bước chạy đồng thời (timestamp bắt đầu gần nhau)

**Tự trả lời được.**
- Thời gian p95 giảm bao nhiêu? **Token có đổi không? Vì sao?**
- Nếu một tool vẫn là hàm blocking, `asyncio.gather` làm gì?

**Cái bẫy.** Bài rất dễ *tưởng* đã xong. Cách duy nhất để biết: log timestamp.

---

### Bài 25 · Lập lại kế hoạch + phá hệ thống có chủ đích 🔴
**~2 ngày**

**Bối cảnh.** Agent ở bài 22–24 chạy tốt khi mọi thứ suôn sẻ. Thực tế: Qdrant tắt giữa chừng, tool timeout, mã mới lên sàn chưa đủ 14 phiên, ngày nghỉ lễ không có giá. Bài này xây khả năng lập lại kế hoạch khi bước thất bại — và định nghĩa "đường thất bại tử tế" khi đã hết lần thử.

**Để hiểu gì.** Khác biệt giữa agent "chạy được khi suôn sẻ" và agent **sống được trong thực tế**. Đây là chỗ bài 20 trả cổ tức.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `agents/replanner.py`.
2. Viết `has_changed(old_plan, new_plan)` để phát hiện khi model sinh kế hoạch y hệt cũ.
3. Tạo `tests/test_agent_chaos.py` với 5 test case — chạy để xem fail hết trước khi fix.

**Chi tiết từng việc:**

- **`agents/replanner.py`** — nhận (kế hoạch gốc + danh sách bước đã thất bại kèm `message` lỗi từ tool) → gọi model sinh kế hoạch sửa đổi. Truyền đầy đủ lịch sử lỗi vào prompt.

- **Giới hạn cứng:** tối đa 2 lần lập lại · tổng không quá 12 bước · không vượt ngân sách token · timeout cả phiên.

- **Đường thoát tử tế** — khi chạm trần, không im lặng, không bịa. Xuất báo cáo một phần với tuyên bố rõ ràng về phần thiếu.

- **Chặn gọi lặp** — trước khi chạy bước, kiểm tra `(executor, params)` có trong `state["history"]` chưa.

- **5 tình huống test:** Qdrant tắt giữa chừng, tool timeout, mã mới lên sàn (< 14 phiên), mã không tồn tại, ngày nghỉ lễ.

- Sau khi tất cả xanh, chạy 30 câu từ `evals/golden_hpg.yaml` + 5 câu edge case, ghi vào `NOTES.md`: tỉ lệ lập lại kế hoạch (%) và tỉ lệ chạm trần số bước (%).

**Xong khi.**
- [ ] 5 tình huống: **không lặp vô hạn, không crash**, cả 5 ra output có ý nghĩa
- [ ] `tests/test_agent_chaos.py` xanh
- [ ] Ghi số: tỉ lệ lập lại kế hoạch và tỉ lệ chạm trần trên 30 câu

**Cái bẫy.** Rất dễ để bộ lập lại kế hoạch sinh ra kế hoạch **y hệt** cái cũ. Phải truyền lịch sử các bước đã thất bại vào, và kiểm tra kế hoạch mới có khác cái cũ.

---

### Bài 26 · So 3 kiến trúc — và dám kết luận trung thực 🔴
**~1 ngày**

**Bối cảnh.** Bạn vừa xây 3 kiến trúc agent khác nhau. Bài này chạy cùng bộ câu hỏi qua cả 3 và so số theo 5 cột. Mục đích không phải tìm kiến trúc "tốt nhất" — mà là học cách chọn kiến trúc theo dữ liệu thật thay vì theo độ phức tạp. Kể cả khi số nói rằng 2 tuần vừa xây là dư.

**Để hiểu gì.** Bài dạy nhiều nhất trong chặng agent — không phải kỹ thuật mà là **cách ra quyết định**: chọn kiến trúc theo số liệu, không theo mức độ thú vị.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `evals/compare_architectures.py`.
2. Phân loại 25 câu trong `evals/golden_hpg.yaml` thành 3 nhóm: `simple | compound | multi_source`.
3. Chạy thử 3 câu (một câu mỗi nhóm) qua cả 3 kiến trúc.

**Chi tiết từng việc:**

- **3 kiến trúc:**
  - `arch_a`: Chỉ pipeline RAG trực tiếp — baseline thật sự đơn giản nhất.
  - `arch_b`: Graph tuần tự từ bài 22.
  - `arch_c`: Planning + Parallel + Replanning từ bài 23–25.

- **5 cột đo:** `quality_score` (LLM-as-judge 1–5), `latency_p95`, `total_tokens`, `cost_usd`, `failure_rate`.

- **Bảng tổng hợp** gồm 2 bảng trong `NOTES.md`:
  - Bảng 1 — Tổng thể (25 câu): 3 kiến trúc × 5 cột.
  - Bảng 2 — Theo nhóm câu hỏi: 3 kiến trúc × 3 nhóm (quality). **Đây là chỗ insight thật nằm.**

- **Sau khi có 2 bảng:** trả lời từng câu và ghi vào `NOTES.md`:
  - Kiến trúc C thắng ở nhóm nào? Thua ở nhóm nào?
  - Nếu C chỉ thắng ở multi_source (20% câu) mà đắt hơn 40% cho tất cả → có nên dùng routing không?
  - **Kiến trúc nào sẽ dùng làm production? Ghi lý do cụ thể.**

- **Cập nhật hệ thống** — dùng kiến trúc mà số liệu chỉ ra.

**Xong khi.**
- [ ] Bảng 3×5, **có bảng chia theo nhóm câu hỏi**
- [ ] Hệ thống cuối cùng dùng **kiến trúc mà số liệu chỉ ra**, không phải kiến trúc phức tạp nhất

**Tự trả lời được.**
- Kiến trúc phức tạp thắng ở nhóm nào, **thua ở nhóm nào?**
- Bạn có sẵn sàng ghi lại kết luận rằng 2 tuần vừa rồi xây một thứ không đáng dùng cho phần lớn trường hợp không?

**Cái bẫy.** Bạn sẽ có thiên hướng bảo vệ thứ mình vừa xây. Để số liệu quyết định.

---

### Bài 27 · Tạm dừng chờ người — sống qua việc restart server 🔴
**~1.5 ngày**

**Bối cảnh.** Agent phân tích tài chính có thể đề xuất hành động — người dùng cần xem và duyệt trước khi thực thi. Hiện tại, state agent lưu trong bộ nhớ: khi server restart, mọi phiên đang chờ duyệt biến mất. Bài này chuyển state sang Postgres.

**Để hiểu gì.** **Lưu trạng thái bền vững** cho luồng chạy dài. Dừng chờ người trong notebook thì ai cũng làm được; sống qua việc process chết mới dạy bạn điều gì đó. **Thiết kế state là thiết kế khả năng khôi phục.**

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo bảng Postgres `agent_sessions` (với `session_id`, `ticker`, `state JSONB`, `status`, `expires_at`, `audit_log JSONB`).
2. Tạo `agents/checkpointer.py` với `save_checkpoint` và `load_checkpoint`. Test bằng cách save rồi load lại.
3. Tích hợp `interrupt()` của LangGraph tại bước trước hành động.

**Chi tiết từng việc:**

- **Lưu ý về state JSONB:** `state` phải là JSONB thuần — đây là lý do bài 22 cảnh báo không nhét DataFrame vào state. Nếu state có object không serialize được, bước save sẽ fail ở đây.

- **3 endpoint API:** `GET /sessions/pending`, `POST /sessions/{id}/approve` (có thể kèm edits), `POST /sessions/{id}/reject`.

- **Timeout 5 phút** — set `expires_at = NOW() + INTERVAL '5 minutes'`. Background task kiểm tra mỗi 60 giây.

- **Test then chốt**: chạy đến interrupt → `docker restart <postgres_container>` → phiên vẫn còn → đồng ý → tiếp tục đúng chỗ.

**Xong khi.**
- [ ] **Test then chốt:** chạy tới lúc dừng → `docker restart` container → phiên vẫn còn → đồng ý → tiếp tục đúng chỗ
- [ ] 2 phiên chờ song song không lẫn nhau
- [ ] Đồng ý sau 6 phút → bị từ chối vì timeout

**Cái bẫy.** Nếu fail, đừng sửa bằng cách ép serialize — sửa bằng cách đưa dữ liệu lớn ra khỏi state.

---

### Bài 28 · Memory: khi nào ghi, và xử lý mâu thuẫn 🔴
**~2 ngày**

**Bối cảnh.** Người dùng nói "tôi theo dõi FPT, HPG và ưa rủi ro thấp" ở phiên 1. Phiên 2 họ không nói lại — nhưng hệ thống nên nhớ. Bài này xây lớp memory lưu sở thích giữa phiên và giải quyết hai vấn đề hay bị bỏ qua: **khi nào ghi** và **khi họ tự mâu thuẫn thì xử lý thế nào**.

**Để hiểu gì.** Phần khó của memory **không phải lưu** — mà là quyết định khi nào ghi và xử lý khi người dùng tự mâu thuẫn.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo bảng Postgres `user_memory` (với `id`, `tenant_id`, `user_id`, `key`, `value JSONB`, `confidence`, `source_message`, `superseded_by`).
2. Tạo `memory/extractor.py` với `extract_preferences(conversation) -> list[MemoryItem]`.
3. Test thủ công với đoạn hội thoại có câu "tôi theo dõi HPG".

**Chi tiết từng việc:**

- **Cửa lọc:** chỉ ghi khi `confidence >= 0.7`. Câu mơ hồ ("chắc là tôi hơi thích ngành thép") phải ra `confidence < 0.7`.

- **Xử lý mâu thuẫn:** khi ghi item mới, đánh dấu record cũ bị thay thế bằng `superseded_by`. Không XOÁ — giữ lại trong DB để audit. Chỉ không đưa vào ngữ cảnh nữa.

- **`memory/reader.py`** — đầu mỗi phiên, chỉ lấy record có `superseded_by IS NULL`, sort theo `confidence DESC`, tối đa 5 item.

- Chạy extractor **sau khi phiên kết thúc**, không trong phiên — tránh câu giả định bị ghi thành sở thích thật.

**Xong khi.**
- [ ] Phiên 1 nói sở thích → bảng có 2 record đúng
- [ ] Phiên 2 (session mới) hỏi → hệ thống biết danh mục, báo cáo đổi giọng
- [ ] Phiên 3 thay đổi sở thích → record cũ bị thay thế, vẫn còn trong DB
- [ ] Câu mơ hồ → **không** được ghi

**Cái bẫy.** Nếu chạy extractor giữa phiên, câu giả định ("nếu tôi ưa rủi ro cao thì sao?") bị ghi thành sở thích thật.

---

### Bài 29 · Memory: quên đi 🔴
**~1.5 ngày**

**Bối cảnh.** Memory ở bài 28 chỉ ghi thêm mà không quên — sau vài trăm phiên, ngữ cảnh bị nhồi đầy sở thích cũ. Bài này xây cơ chế quên có kiểm soát: memory cũ mờ dần, chỉ những gì liên quan đến câu hỏi hiện tại mới được đưa vào ngữ cảnh.

**Để hiểu gì.** Memory chỉ ghi thêm mà không quên sẽ **tự đầu độc** sau vài trăm lượt.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo Qdrant collection `episodic_memory`.
2. Tạo `memory/episodic.py` với `store_episode` và `retrieve_similar`.
3. Test: store 5 episode giả, retrieve bằng câu tương tự — phải ra đúng.

**Chi tiết từng việc:**

- **Episodic storage** — mỗi phiên thành công lưu vào Qdrant: câu hỏi gốc, tóm tắt kế hoạch, kết luận, feedback người dùng.

- **Cơ chế quên — 3 lớp:**
  1. **Thời hạn:** episodic memory hết hạn sau 90 ngày.
  2. **Điểm suy giảm:** nhân score với `decay = exp(-days_old / 30)`.
  3. **Giới hạn cứng:** chỉ lấy top 3 vào ngữ cảnh.

- **`memory/procedural.py`** — sinh rule từ feedback người dùng. **Chỉ ghi khi có tín hiệu ngoài** (người dùng chủ động), không khi model tự "cảm thấy".

- **Kiểm tra tải** — giả lập 200 item memory cho một user: ngữ cảnh không được phình (vẫn ≤ 3 item), đo quality không tụt.

**Xong khi.**
- [ ] 20 phiên giả lập → lấy về đúng những lần liên quan
- [ ] Bật/tắt episodic memory → chạy eval → **có số** cho biết nó giúp hay không
- [ ] Nhồi 200 item → ngữ cảnh **không phình**, chất lượng không tụt

**Cái bẫy.** Episodic memory rất dễ **giảm** chất lượng vì nó dạy model sai hướng. Phải đo, đừng giả định.

---

### Bài 30 · Đo memory + test rò rỉ giữa người dùng 🔴
**~1.5 ngày**

**Bối cảnh.** Memory có hai loại lỗi thầm lặng: nhớ sai thứ chưa từng nói (bịa sở thích) và rò rỉ sở thích người dùng A sang người dùng B. Cả hai không gây crash. Bài này đo cả hai và tự tấn công để kiểm chứng cách ly.

**Để hiểu gì.** Hai thứ gần như không ai làm: **đo memory** và **test cách ly memory**. **Nhớ sai nguy hiểm hơn không nhớ.**

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `evals/memory_multi_session.yaml` với 10 tình huống × 3 phiên.
2. Tạo `tests/test_memory_isolation.py` với 3 test case. Chạy để xem fail hết trước khi có isolation code.
3. Chạy 2 tình huống đầu thủ công để hiểu format.

**Chi tiết từng việc:**

- **Đo 2 chỉ số:**
  1. **Recall** (nhớ đúng thứ đã nói): kiểm tra `expected_remembered` có xuất hiện trong báo cáo phiên 3 không.
  2. **Precision** (không bịa thứ chưa nói): kiểm tra `expected_NOT_mentioned` **không** xuất hiện trong báo cáo. Chỉ số này khó hơn — phải thiết kế tình huống sao cho nếu hệ thống "bịa" thì phần tử sẽ xuất hiện.

- **3 test isolation:** người dùng A không thể thấy memory của B, hai user có sở thích gần giống nhau không bị lẫn, không rò rỉ cross-tenant.

- Ghi 2 con số vào `NOTES.md`: `memory_recall = X%` và `memory_precision = Y%`.

**Xong khi.**
- [ ] 10 tình huống chạy tự động, ra 2 con số
- [ ] Test cách ly xanh cả 3 trường hợp

**Tự trả lời được.**
- Hai chỉ số của bạn là bao nhiêu? Vì sao chỉ số thứ hai (không bịa) **nguy hiểm hơn** khi thấp?
- Làm sao bạn *đo* được việc "không bịa"?

**Cái bẫy.** Với sản phẩm tài chính, nhớ sai một sở thích chưa từng nói ra khiến hệ thống đưa khuyến nghị lệch mà người dùng không hiểu tại sao.
