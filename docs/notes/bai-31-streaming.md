# Bài 31 · Streaming — chữ hiện từng từ thay vì chờ hết

## Thiết kế

### SSE format
Mỗi event gồm `event:` và `data:` trên 2 dòng riêng, kết thúc `\n\n`:
```
event: status
data: {"step": "loading_history"}

event: status
data: {"step": "streaming"}

data: {"text": "Hòa "}
data: {"text": "Phát "}
...
: heartbeat

event: done
data: {"saved": true, "length": 342}
```

### Luồng xử lý `stream_turn()`
1. Yield `event: status` → `loading_history`
2. Load history + user_memory (sync)
3. Yield `event: status` → `streaming`
4. Tạo thread chạy `client.stream()` (sync), đẩy chunk vào `asyncio.Queue`
5. `asyncio.wait_for(queue.get(), timeout=15)` → yield `data: {text: chunk}` hoặc `heartbeat`
6. Nếu `asyncio.CancelledError` (client disconnect) → thread.join(), return mà không save_turn
7. Sau khi stream xong → `save_turn()` + `extract_preferences()` + yield `event: done`

### Endpoint
```
POST /conversations/{conversation_id}/messages/stream
Content-Type: application/json
→ StreamingResponse, media_type="text/event-stream", X-Accel-Buffering: no
```

### Tại sao thread + asyncio.Queue?
`client.stream()` là sync Iterator (OpenAI SDK). FastAPI async endpoint cần async generator. Giải pháp: chạy sync stream trên thread riêng, đẩy chunk qua thread-safe `loop.call_soon_threadsafe(queue.put_nowait, chunk)`.

### Header `X-Accel-Buffering: no`
Nginx mặc định buffer toàn bộ response rồi trả cùng lúc → streaming vô nghĩa. Header này báo nginx không buffer, trả ngay từng chunk.

### Tại sao không save_turn khi cancel?
Response một phần trong history sẽ làm model nhầm lẫn ở turn tiếp theo (nghĩ mình đã hoàn thành câu trả lời).

## Checklist "Xong khi"
- [x] `curl --no-buffer` thấy chunk từng cái: `make curl-stream-b31 CID=<id> MSG="..."`
- [x] Turn 2 cùng `conversation_id` → agent thấy history turn 1
- [x] Disconnect giữa chừng → turn không được save (`test_disconnect_mid_stream_turn_not_saved`)
- [ ] Streamlit: `st.write_stream()` — cần chạy thêm bước UI

## Lệnh chạy
```bash
make test-b31       # pytest 4 test cases
make api-b31        # uvicorn port 8031
make curl-stream-b31 CID=<id> MSG="HPG là gì?"
```

## Kết quả test
Chạy: `pytest tests/test_bai31_streaming.py -v -s` — **4/4 xanh** (59s)

| Test | Kết quả |
|------|---------|
| `test_chunks_arrive_incrementally` | 70 chunks, done.saved=True, length=182 ✓ |
| `test_turn2_sees_turn1_history` | Turn 2 nhắc lại "câu trước...HPG" ✓ |
| `test_done_event_saved_true` | `{"saved": True, "length": N}` ✓ |
| `test_disconnect_mid_stream_turn_not_saved` | history=0 sau cancel ✓ |

### Bug đã fix
1. `_parse_done` fail vì generator yield cả event block (`"event: done\ndata: {...}\n\n"`) dưới dạng 1 string — parser cần split(`\n`) trước khi so sánh.
2. Thread gọi `loop.call_soon_threadsafe` sau khi loop đã đóng (sau CancelledError) → wrap trong `try/except RuntimeError`.
