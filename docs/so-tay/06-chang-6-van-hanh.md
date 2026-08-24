# CHẶNG 6 · Vận hành (tuần 10–12)

> Chuyển từ "chạy được trên máy mình" sang "chạy được cho người thật, 24/7".

---

### Bài 31 · Streaming — chữ hiện từng từ thay vì chờ hết 🔴
**~1 ngày**

**Bối cảnh.** Câu trả lời mất 8–10 giây. Người dùng thấy spinner im lặng rồi text xuất hiện một lần — trải nghiệm tệ. Streaming hiện từng mảnh output ngay khi model tạo ra, giúp người dùng cảm thấy hệ thống phản hồi nhanh hơn nhiều so với thực tế.

**Để hiểu gì.** Vì sao streaming là yêu cầu UX không thể thiếu với LLM — và hai bẫy kỹ thuật hay gặp.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Thêm endpoint streaming vào `api/routes.py`:
   ```python
   from fastapi.responses import StreamingResponse

   @router.get("/query/stream")
   async def stream_query(ticker: str, question: str):
       return StreamingResponse(
           generate(ticker, question),
           media_type="text/event-stream",
           headers={"X-Accel-Buffering": "no"},
       )
   ```
2. Test bằng `curl --no-buffer` để thấy chunk xuất hiện từng cái.

**Chi tiết từng việc:**

- **SSE format** — mỗi event gồm `event:` và `data:` trên 2 dòng, kết thúc bằng `\n\n`. Gửi status event **trước** khi gửi content: `event: status\ndata: {"step": "retrieving"}\n\n`.

- **Heartbeat mỗi 15 giây** — nếu bước nào đó kéo dài (reranker, model gọi ngoài), proxy/browser timeout kết nối. Gửi comment SSE định kỳ: `: heartbeat\n\n`.

- **Phát hiện client ngắt kết nối** — dùng `asyncio.CancelledError`:
  ```python
  try:
      async for chunk in model.astream(...):
          yield f"data: {chunk}\n\n"
  except asyncio.CancelledError:
      await cancel_running_task()  # hủy task tốn tiền đang chạy
      return
  ```

- **Không bao giờ cache kết quả streaming một phần.** Cache chỉ dùng cho kết quả hoàn chỉnh.

- **Test Streamlit** — thêm `st.write_stream()` vào UI, demo cho đồng nghiệp.

**Xong khi.**
- [ ] `curl --no-buffer` thấy chunk xuất hiện từng cái, không đợi hết
- [ ] Ngắt kết nối giữa chừng → server không tiếp tục gọi model, token usage dừng

**Tự trả lời được.**
- Header `X-Accel-Buffering: no` làm gì và cần thiết khi nào?
- Vì sao ngắt kết nối phải huỷ task đang chạy?

**Cái bẫy.** Quên `X-Accel-Buffering: no` khi có nginx ở giữa — buffer gom tất cả rồi trả cùng lúc, streaming vô nghĩa.

---

### Bài 32 · Cache: đúng và sai ở đâu 🔴
**~1.5 ngày**

**Bối cảnh.** Cache giảm chi phí và latency nhưng trả kết quả cũ cho câu hỏi mới là lỗi nghiêm trọng với dữ liệu tài chính. Bài này xây 2-tier cache với key chính xác, và thực nghiệm để xác định khi nào vector similarity cache **gây hại** thay vì giúp.

**Để hiểu gì.** Cache sai nguy hiểm hơn không cache — đặc biệt với dữ liệu tài chính.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `core/cache.py` với `CacheKey` Pydantic model.
2. Test key generation: `key("hpg", "doanh thu q2 2024?", "v1", "claude-sonnet")`.
3. Đặt TTL ngắn (30 giây) để test nhanh.

**Chi tiết từng việc:**

- **Cache key phải gồm tất cả yếu tố ảnh hưởng kết quả:**
  ```python
  class CacheKey(BaseModel):
      tenant_id: str
      ticker: str
      normalized_question: str  # lowercase, bỏ dấu, bỏ dấu câu
      prompt_version: str
      model_version: str
  ```
  Thiếu `tenant_id` → user A thấy kết quả của user B. Thiếu `prompt_version` → thay prompt không có tác dụng.

- **2-tier cache:**
  - Tier 1 (exact): hash SHA-256 của key → cache hit chắc chắn đúng.
  - Tier 2 (vector): embedding của câu hỏi → tìm câu tương tự đã cache.

- **Thực nghiệm: HPG vs HSG — vector cache có thể gây hại.** Hai mã này có sản phẩm/ngành giống nhau, cosine similarity của câu hỏi tương tự rất cao. Nếu dùng threshold cosine thuần túy, có thể trả kết quả HPG cho câu hỏi về HSG. Vector cache tier 2 **phải trích ticker từ câu hỏi** và so khớp ticker trước — không chỉ so similarity.

- **TTL theo giờ:**
  - 2 phút khi thị trường đang mở (9:00–14:45 ngày thường)
  - 30 phút ngoài giờ giao dịch

**Xong khi.**
- [ ] Demo HPG vs HSG: gõ câu hỏi về HSG sau khi cache HPG → **không trả nhầm**
- [ ] Thay prompt version → cache bị invalidate đúng

**Tự trả lời được.**
- Nếu bỏ `ticker` khỏi cache key vector tier, điều gì xảy ra?
- Vì sao TTL ngắn hơn trong giờ giao dịch?

**Cái bẫy.** Vector cache dựa thuần vào cosine similarity không đủ cho dữ liệu tài chính theo mã cụ thể.

---

### Bài 33 · Chaos engineering — phá hệ thống có chủ đích 🔴
**~1.5 ngày**

**Bối cảnh.** Hệ thống hiện tại chưa biết nó thất bại ra sao trong thực tế. Phá có kiểm soát thì biết trước và chuẩn bị. Phá không kiểm soát trong production thì người dùng là người đầu tiên phát hiện.

**Để hiểu gì.** Chaos engineering nghĩa là phá có chủ đích để biết hệ thống thất bại ra sao, **và chuẩn bị trước thay vì phản ứng sau.**

**Làm gì.**

**Bắt đầu từ đâu:**
1. Tạo `tests/test_chaos.py` với 5 test case, tất cả đều fail.
2. Điền cột "expected" trong bảng dưới **trước khi** tắt bất kỳ service nào.
3. Tắt từng service một, ghi kết quả thực tế.

**Chi tiết từng việc:**

- **5 tình huống, điền kết quả thực tế vào bảng:**

  | Service tắt | Expected | Thực tế | Thời gian phát hiện |
  |---|---|---|---|
  | Qdrant | Trả kết quả SQL-only nếu có, báo rõ không có context | ? | ? |
  | Redis | Query chậm hơn, không crash | ? | ? |
  | Postgres | Không có tính năng SQL, báo rõ | ? | ? |
  | Price provider | Trả phân tích cơ bản không có giá thật, báo rõ | ? | ? |
  | Price tool timeout | ToolResult `upstream_error`, agent không lặp | ? | ? |

- **Circuit breaker:** khi một service fail quá X lần trong Y giây, mở circuit (ngưng gọi service đó cho đến khi health check pass). Không để retry loop tiêu hết budget token.

- **Retry với jitter:** `time.sleep(base * 2**attempt + random.uniform(0, 1))`. Không retry đồng loạt làm quá tải service vừa phục hồi.

- **Time budget per request** — đặt deadline toàn phiên. Khi gần hết, hoàn thành bước hiện tại và trả partial report thay vì timeout hard.

**Xong khi.**
- [ ] 5 tình huống: **không có 500 cứng**, mỗi tình huống ra output có ý nghĩa
- [ ] Điền được cột "thực tế" sau khi test

**Tự trả lời được.**
- Vì sao phải điền "expected" **trước** khi phá?
- Khi nào nên dùng graceful degradation thay vì hard fail?

**Cái bẫy.** "Graceful degradation" không có nghĩa là trả câu trả lời không đầy đủ mà không nói với người dùng. Phải nói rõ phần nào thiếu và lý do.

---

### Bài 34 · Load test — tìm điểm nghẽn thật 🟡
**~1 ngày**

**Bối cảnh.** Không biết hệ thống chịu được bao nhiêu người dùng đồng thời — chỉ biết 1 người chạy ổn. Bài này dùng k6 để tăng tải từ từ, tìm điểm nghẽn, và xác định điểm nghẽn có phải CPU hay không (thường không phải).

**Để hiểu gì.** Tìm điểm nghẽn thật thay vì đoán. Với hệ thống AI, điểm nghẽn **gần như không bao giờ là CPU** mà là rate limit API hoặc reranker.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Cài k6: `winget install k6` (Windows) hoặc `brew install k6` (Mac).
2. Viết script k6 đơn giản với 5 VU và chạy thử 30 giây.
3. Xem dashboard, đảm bảo thấy được p50/p95.

**Chi tiết từng việc:**

- **Script k6 với 3 giai đoạn:**
  ```javascript
  export const options = {
    stages: [
      { duration: '1m', target: 10 },   // warm up
      { duration: '3m', target: 25 },   // steady state
      { duration: '2m', target: 50 },   // stress
    ],
    thresholds: {
      http_req_duration: ['p95<10000'],  // 10s
      http_req_failed: ['rate<0.01'],    // <1% lỗi
    },
  }
  ```

- **Hai kịch bản:** cold cache (mỗi request câu hỏi khác nhau) và hot cache (cùng 10 câu hỏi). So sánh p50/p95/p99 và throughput giữa hai kịch bản.

- **Tìm điểm rate-limit, không phải điểm CPU.** Khi p95 bắt đầu tăng mạnh, xem log của từng thành phần — thường là rate limit của LLM provider (HTTP 429) hoặc reranker model bị bottleneck.

- **Nếu reranker là bottleneck:** tách thành service riêng với instance pool, không chạy trong main FastAPI process. Ghi vào `NOTES.md`.

**Xong khi.**
- [ ] Bảng: cold vs hot × (p50, p95, p99, throughput, điểm nghẽn)
- [ ] Nói được: *"điểm nghẽn là X, không phải CPU, tìm ra bằng trace"*

**Tự trả lời được.**
- Điểm nghẽn là gì? Bạn tìm ra bằng cách nào?
- Vì sao tốc độ cold cache và hot cache khác nhau nhiều hay ít?

**Cái bẫy.** Scale thêm instance khi chưa biết điểm nghẽn là lãng phí — và không hiệu quả nếu điểm nghẽn là rate limit API bên ngoài.

---

### Bài 35 · Tính năng enterprise: SSO, audit log, quota 🟡
**~1.5 ngày**

**Bối cảnh.** Khách hàng doanh nghiệp yêu cầu ba thứ không thương lượng được: đăng nhập qua hệ thống của họ (SSO), bằng chứng về hành động mọi người đã làm (audit log), và kiểm soát chi phí theo phòng ban (quota). Bài này xây cả ba.

**Để hiểu gì.** Đây là checklist enterprise thật. Một trong ba cái này thiếu → không ký được hợp đồng.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Chạy Keycloak local:
   ```bash
   docker run -p 8080:8080 -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin quay.io/keycloak/keycloak:latest start-dev
   ```
2. Tạo realm `hpg-rag`, tạo 3 user cho 3 role.
3. Test login thủ công trước khi tích hợp.

**Chi tiết từng việc:**

- **SSO với Keycloak** — 3 role: `analyst` (chỉ query), `approver` (approve agent action từ bài 27), `admin` (quản lý tenant). Middleware FastAPI check JWT token và role.

- **Audit log append-only** — mọi query, mọi action, mọi thay đổi cấu hình đều ghi vào bảng `audit_log` với `JSONB payload`. Không cho phép sửa/xoá:
  ```sql
  REVOKE UPDATE, DELETE ON audit_log FROM app_user;
  ```
  Định kỳ kiểm tra bảng không bị xoá bằng cách so count với thống kê.

- **Tenant quota trong Redis:**
  ```python
  # Kiểm tra quota trước mỗi request
  key = f"quota:{tenant_id}:{today}"
  count = redis.incr(key)
  redis.expire(key, 86400)  # reset hàng ngày
  if count > tenant.daily_quota:
      raise HTTPException(429, "Daily quota exceeded")
  ```

- **MinIO event webhook** — khi file mới upload lên MinIO bucket, trigger auto-index pipeline (Dagster job từ bài 13).

**Xong khi.**
- [ ] Analyst không gọi được approve endpoint (403), không phải 401
- [ ] Query → dòng audit log tạo ra → không thể xoá dù là admin
- [ ] Vượt quota → 429 rõ ràng

**Tự trả lời được.**
- Vì sao phải `REVOKE UPDATE DELETE` thay vì chỉ tin tưởng app code?
- 401 vs 403 — khác nhau ở đâu?

**Cái bẫy.** Dùng `DELETE FROM audit_log WHERE ...` để "dọn dẹp" log cũ là phá vỡ toàn bộ mục đích audit. Dùng partition archiving thay thế.

---

### Bài 36 · Deploy lên VM thật — cổng ra production 🔴
**~2 ngày**

**Bối cảnh.** Chạy trên máy dev không đủ. Bài này deploy lên VM thật bằng Terraform, đóng gói bằng Docker multi-stage, và cài CI/CD gate: không deploy nếu eval tụt.

**Để hiểu gì.** Deploy thật khác local ở ba điểm: secret management, health check để orchestrator biết khi nào restart, và eval gate để không đưa regression vào production.

**Làm gì.**

**Bắt đầu từ đâu:**
1. Viết `Dockerfile` multi-stage đơn giản trước.
2. Test build local: `docker build -t hpg-rag:test .`
3. Chạy container và verify health endpoint: `curl http://localhost:8000/health/live`.

**Chi tiết từng việc:**

- **Dockerfile multi-stage:**
  ```dockerfile
  FROM python:3.12-slim AS builder
  WORKDIR /app
  COPY pyproject.toml uv.lock ./
  RUN pip install uv && uv sync --frozen --no-dev

  FROM python:3.12-slim
  WORKDIR /app
  COPY --from=builder /app/.venv .venv
  COPY . .
  ENV PATH="/app/.venv/bin:$PATH"
  CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
  Secret **không được bake vào image** — inject qua biến môi trường hoặc secret manager lúc runtime.

- **Hai health endpoint riêng biệt:**
  - `GET /health/live` — chỉ trả 200 nếu process còn sống (không check dependencies). Orchestrator dùng để quyết định có restart không.
  - `GET /health/ready` — check Qdrant, Postgres, Redis có kết nối được không. Orchestrator dùng để quyết định có route traffic vào không.

- **CI/CD với eval gate** — pipeline gồm:
  ```
  test → build → eval → deploy
  ```
  Bước `eval` chạy `python evals/run.py --skip-ragas` và `python evals/check_thresholds.py`. Nếu exit code ≠ 0 thì **không deploy**. Ghi vào `NOTES.md`: lần đầu gate bắt được regression nào.

- **Terraform** — tạo VM, security group, attach volume. State file lưu remote (S3 hoặc Terraform Cloud), không commit vào git.

**Xong khi.**
- [ ] Deploy bằng `git push` → pipeline chạy → image build → eval pass → deploy tự động
- [ ] Ép eval fail → **pipeline dừng, không deploy**
- [ ] Xoá secret khỏi image, inject lúc runtime

**Tự trả lời được.**
- Vì sao liveness và readiness phải là **hai endpoint riêng**?
- Vì sao không commit secret vào `.env` trong git?

**Cái bẫy.** Nếu readiness check Qdrant bị timeout, orchestrator restart container liên tục dù container hoàn toàn khoẻ — readiness check phải có timeout ngắn (≤ 2 giây).
