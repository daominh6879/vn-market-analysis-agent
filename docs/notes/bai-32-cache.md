# Bài 32 — Cache: đúng và sai ở đâu

## Thiết kế

**2-tier cache** trong `core/cache.py`:

| Tier | Kỹ thuật | Key | TTL |
|------|----------|-----|-----|
| 1 — exact | SHA-256(CacheKey JSON) → Redis | `cache:b32:exact:{hash}` | 120s giờ TT, 1800s ngoài giờ |
| 2 — vector | embed(normalized_question) → Qdrant `cache_vectors` | uuid5(hash) | expires_at payload |

**CacheKey** gồm: `tenant_id`, `intent`, `ticker`, `normalized_question`, `prompt_version`, `model_version`.
Không có `conversation_id` — cache cross-conversation.

**`normalized_question` chỉ dùng cho RAG intents** (`rag_qa`, `screening`) vì câu hỏi khác nhau → RAG retrieve khác → kết quả khác. Pure-tool intents (`price_action`, `technical_analysis`, `news_sentiment`, `macro_sector`, `investment_case`) để `normalized_question=""` — same tools always run regardless of phrasing.

**`fundamentals` + `qa_document` → merge thành `rag_qa`**: cả 2 đều gọi `rag/qa.py`, không có lý do tách. `_dispatch_intent` gọi thẳng `rag.qa.answer(user_message, ticker=ticker)`.

**normalize**: lowercase + NFKD diacritic strip + bỏ ký tự đặc biệt.

## Tích hợp vào chat agent

Điểm tích hợp: `memory/turn_handler.py`

- `run_turn`: check cache trước LLM call; set cache sau khi có reply.
- `stream_turn`: check cache sau routing (có `route.ticker`); emit `event: status step=cache_hit tier=exact|vector` rồi stream cached reply.

**Quy tắc khi nào KHÔNG cache:**
- `len(history) > 0` → `make_cache_key` trả `None` → skip hoàn toàn.
- Turn 2 trở đi: `history` không rỗng → không cache, không lookup.

## Ticker guard (ngăn HPG/HSG cross-cache)

Tier 2 (vector) phải qua 3 guards:
1. `payload.ticker == ck.ticker` — ticker phải khớp chính xác.
2. `payload.tenant_id == ck.tenant_id` — tenant isolation.
3. `payload.expires_at > time.time()` — không trả kết quả hết hạn.

Tại sao cần: HPG và HSG ngành thép giống nhau, cosine similarity câu hỏi có thể > 0.92.
Nếu chỉ dùng threshold thuần túy → trả kết quả HPG cho câu hỏi HSG → sai nghiêm trọng.

## TTL theo giờ giao dịch

```python
# Mon-Fri 09:00–14:45 VN time → 120s
# Ngoài giờ → 1800s
```

Lý do: giờ giao dịch dữ liệu giá, dòng tiền thay đổi từng phút. 2 phút là đủ để không trả giá cũ quá lâu mà vẫn giảm LLM calls.

## Test cases

```bash
# Chạy unit tests (không cần LLM/network):
make test-b32-unit

# Chạy tất cả (cần Redis + LLM + tools đang chạy):
make test-b32
```

| Test | Mục đích |
|------|----------|
| `test_hpg_hsg_no_cross_hit` | Exact tier: HPG key không match HSG |
| `test_same_ticker_different_intent_no_cross_hit` | technical_analysis HPG không hit fundamentals HPG |
| `test_vinamilk_vnm_same_cache_hit` | Sau router resolve ticker=VNM, vinamilk và VNM share 1 cache entry |
| `test_make_cache_key_turn1_pure_tool` | Pure-tool intent: normalized_question="" |
| `test_make_cache_key_turn1_rag` | RAG intent: normalized_question được set |
| `test_prompt_version_invalidates_exact` | Thay prompt_version → hash khác → miss |
| `test_make_cache_key_turn2_returns_none` | Turn 2 → `make_cache_key` trả `None` |
| `test_cache_hit_second_request_real` | 2 conversation, câu hỏi giống → second hit cache |
| `test_turn2_no_cache_real` | Turn 2 cùng conversation → không hit cache |
| `test_hpg_hsg_no_cross_cache_real` | Cache HPG xong hỏi HSG → không trả nhầm |

## Lệnh tái tạo

```bash
# Khởi động services
docker compose up -d

# Chạy API
make api-b32  # port 8032

# Unit tests (fast)
make test-b32-unit

# Integration tests (slow, ~60-120s tổng)
make test-b32
```

## Logging

Dùng Python `logging` module (`log = logging.getLogger(__name__)`).

| Logger | Event | Level |
|--------|-------|-------|
| `core.cache` | `cache.exact.hit/miss/error` | DEBUG/WARNING |
| `core.cache` | `cache.exact.set/set_error` | DEBUG/WARNING |
| `core.cache` | `cache.vector.hit/miss/skip/guard_fail/error/set/set_error` | DEBUG/WARNING |
| `memory.turn_handler` | `cache.hit conv=… intent=… ticker=… tier=…` | INFO |
| `memory.turn_handler` | `cache.miss conv=… intent=… ticker=…` | DEBUG |

Cache hit → `log.info` + `update_current_trace(cache_hit=True, cache_tier=…)` vào Langfuse.
Cache miss → `log.debug`.
`_dispatch_intent` chỉ chạy trên miss → Langfuse trace của nó luôn có `cache_hit: False`.

Bật debug log:
```bash
LOG_LEVEL=DEBUG python -m uvicorn api.main:app --reload
```

## Ghi chú kỹ thuật

- Redis package: `redis==8.1.0` (cài thêm, chưa có trong requirements.txt ban đầu).
- Qdrant collection `cache_vectors` tự tạo nếu chưa có (dim lấy từ bge-m3 = 1024).
- `cache_set` fail silently — cache miss là graceful degradation, không crash agent.
- `CACHE_PROMPT_VERSION` env var (default `v1`) — tăng lên `v2` để invalidate toàn bộ cache khi thay prompt.
- `CACHE_VECTOR_THRESHOLD` env var (default `0.92`) — threshold cosine cho tier 2.
