# Bài 29 · Memory: quên đi

## Kết quả chạy thực

Tất cả 9 test case pass, thời gian ~120s (real LLM + Qdrant).

```
9 passed in 119.97s
```

## Kiến trúc

### Episodic memory — Qdrant collection `episodic_memory`

Mỗi conversation kết thúc → gọi `finish_conversation()` → lưu vào Qdrant:
- Vector: embed(`first_question + "\n" + summary`)
- Payload: `conversation_id`, `user_id`, `first_question`, `summary`, `conclusion`, `feedback`, `created_at`

### 3 lớp quên

| Lớp | Cơ chế |
|-----|--------|
| Thời hạn | Episode > 90 ngày bị lọc tại retrieval |
| Decay | `score × exp(-days_old / 30)` — 30 ngày giảm còn ~37%, 60 ngày còn ~14% |
| Giới hạn cứng | Chỉ lấy top 3 vào ngữ cảnh |

### Retrieval đầu conversation mới

- `run_turn(..., is_first_turn=True)` → `retrieve_similar(user_message, user_id, top_k=3)`
- Inject vào system prompt (không vào `state["messages"]` — tránh phình context)

### Procedural memory

- `memory/procedural.py` — `generate_rules(feedback_message)`
- Chỉ sinh rule khi có **tín hiệu ngoài** (user chủ động phản hồi)
- Không ghi khi model tự "cảm thấy"

## Files thêm mới

| File | Mục đích |
|------|----------|
| `memory/episodic.py` | store_episode + retrieve_similar với decay |
| `memory/procedural.py` | Sinh rule từ feedback người dùng |
| `infra/migrations/029_episodic.sql` | Ghi chú — collection tạo tự động trong Qdrant |
| `tests/test_bai29_episodic.py` | 9 test cases, real LLM + Qdrant |

## Thay đổi hiện có

`memory/turn_handler.py` — thêm:
- Param `is_first_turn: bool = False`
- Inject episodic context khi `is_first_turn=True`
- Hàm `finish_conversation()` — gọi sau khi conversation kết thúc

## Lệnh chạy

```bash
make test-b29           # chạy 9 test
make test-b29-load      # chỉ test load 20 episodes
```

## Xong khi

- [x] 20 conversation giả lập → episode mới retrieve đúng những conversation liên quan
- [x] Bật/tắt episodic memory (is_first_turn) → inject episodic context vào system prompt
- [x] Nhồi 200 episode (test với 20) → ngữ cảnh không phình (≤ 3 episode)
- [x] Decay: episode cũ 60 ngày score thấp hơn episode mới
- [x] Expiry: episode > 90 ngày bị loại khỏi retrieval
- [x] User isolation: user B không thấy episode của user A
- [x] Procedural rules chỉ sinh từ explicit feedback

## Quan sát

- `client.search()` deprecated trong qdrant-client mới → phải dùng `client.query_points()` → `.points`
- bge-m3 embed dim = 1024 (hardcoded, nhất quán với phần còn lại của codebase)
- Decay half-life = 30 ngày: sau 1 tháng episode còn ~37% trọng số, đủ để vẫn gợi nhớ nhưng không áp đảo nội dung mới
