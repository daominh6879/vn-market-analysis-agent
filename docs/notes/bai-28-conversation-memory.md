# Bài 28 · Conversation + Memory — Kết quả thực

**Ngày:** 29/08/2026  
**Stack:** Postgres · DeepSeek · psycopg2 · FastAPI · pytest

---

## Kiến trúc

```
POST /conversations/{id}/turn
        │
        ├─ load_history(conversation_id, limit=10)   → state["messages"]
        ├─ load_user_memory(user_id, max_items=5)     → system prompt
        ├─ LLM generate reply
        ├─ save_turn(conversation_id, user, assistant)
        └─ extract_preferences(turn_messages)         → save if confidence >= 0.7
```

## File tạo mới

| File | Mục đích |
|------|---------|
| `migrations/028_conversations.sql` | Bảng `conversations`, `messages`, `user_memory` |
| `memory/__init__.py` | Package |
| `memory/conversation.py` | `create_conversation`, `load_history`, `save_turn` |
| `memory/reader.py` | `load_user_memory`, `save_memory_item` (supersede logic) |
| `memory/extractor.py` | LLM-based preference extraction, confidence filter |
| `memory/turn_handler.py` | Orchestrates full turn flow |
| `api/conversations.py` | REST endpoints (POST /conversations, POST /turn, GET /history, GET /memory) |
| `tests/test_bai28_conversation.py` | 9 test cases, real LLM + DB |

## File thay đổi

| File | Thay đổi |
|------|---------|
| `agents/state.py` | Thêm `conversation_id: str` và `messages: list[dict]` |
| `api/main.py` | Include `conv_router` |

## Schema DB

```sql
conversations (conversation_id UUID PK, user_id, tenant_id, created_at)
messages      (message_id UUID PK, conversation_id FK, role, content, agent_session_id FK NULLABLE, created_at)
user_memory   (id UUID PK, tenant_id, user_id, key, value JSONB, confidence, source_message, superseded_by FK NULLABLE)
```

## Quy tắc quan trọng

**Khi nào ghi memory:**
- `confidence >= 0.7` → ghi
- Câu mơ hồ ("chắc là", "nếu như") → confidence < 0.7 → không ghi
- Chạy extractor **sau khi turn kết thúc** — tránh ghi câu giả định

**Xử lý mâu thuẫn:**
- Ghi item mới với cùng `key` → đánh dấu `superseded_by = new_id` trên record cũ
- Record cũ **không xoá** — giữ để audit

**History limit:**
- Chỉ load 10 turn gần nhất → `limit * 2` messages (mỗi turn = 2 messages)
- Tuyệt đối không load toàn bộ lịch sử

## Cách chạy migration

```bash
psql -U $POSTGRES_USER -d $POSTGRES_DB -f migrations/028_conversations.sql
```

## Cách chạy test

```bash
python -m pytest tests/test_bai28_conversation.py -v -s
# Hoặc chạy trực tiếp:
python tests/test_bai28_conversation.py
```

## Checklist "Xong khi"

- [ ] `POST /conversations` → trả `conversation_id`
- [ ] Turn 1 hỏi về FPT → `messages` có 1 cặp; turn 2 → agent thấy lịch sử turn 1
- [ ] Conversation A và B không lẫn lịch sử
- [ ] Phiên 1 nói sở thích → bảng `user_memory` có record đúng
- [ ] Conversation mới cùng user → agent vẫn biết sở thích từ phiên trước
- [ ] Thay đổi sở thích → record cũ bị `superseded_by`, vẫn còn trong DB
- [ ] Câu mơ hồ → **không** được ghi
