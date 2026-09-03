# Plan — token-level streaming cho graph agent

## Bối cảnh (Item 2)

Hiện tại:
- `agents/graph.py:511` — `app.invoke(initial)` chạy graph **đồng bộ, chặn tới khi xong toàn bộ turn**. Không có `stream()`/`astream()` nào ở tầng graph.
- Node synthesize (`agents/graph.py:446`) gọi `client.generate(...)` — chặn, trả full text 1 lần.
- SSE hiện có (`memory/turn_handler.py`, `tracing.py`) chỉ emit **event theo node**: `turn_start`, `llm` (bắt đầu/kết thúc 1 lời gọi generate), `tool`, `gate`, `done`. Đây là log-level event, không phải token delta — client thấy "tool chip" và "LLM đang chạy" nhưng không thấy chữ chạy dần.
- Đã có tiền lệ token streaming thật ở **bài 31** (`api/conversations.py:101`, `memory/turn_handler.py`) nhưng đó là path đơn giản: gọi trực tiếp `client.stream()`, không qua LangGraph, không có tool-calling/routing.

Gap: graph agent (routing + tools + gate + synthesize) không có con đường để đẩy token delta ra ngoài trong lúc node đang chạy.

## Mục tiêu

Khi graph chạy tới node synthesize (hoặc bất kỳ node nào gọi LLM để sinh output cuối trả cho user), text phải chảy ra SSE theo từng chunk — giống UX bài 31 — mà vẫn giữ được tool calls / gate / routing của graph.

## Vướng mắc chính

1. `app.invoke()` là blocking call — không có hook để lấy dữ liệu giữa lúc chạy.
2. `client.generate()` trả `LLMResponse` nguyên khối; muốn stream phải đổi sang `client.stream()` (đã có sẵn trong `llm/base.py`, mọi provider đã implement `stream()`).
3. LangGraph node function là sync, trả `dict` cập nhật state — không tự nhiên "yield" ra ngoài trong lúc chạy.
4. Không dùng LangChain Runnable/ChatModel nên không có `stream_mode="messages"` free — client tự viết (`llm/openai_client.py`, `anthropic_client.py`, ...), không đi qua LangChain callback.

## Hướng giải quyết

### A. Graph: `invoke()` → `astream(stream_mode="custom")`
LangGraph hỗ trợ custom stream events qua `get_stream_writer()` gọi **từ trong node**, không cần đổi toàn bộ node sang async hoặc dùng LangChain model. Node vẫn sync, chỉ cần:
```python
from langgraph.config import get_stream_writer

def node_synthesize(state):
    writer = get_stream_writer()
    ...
    for chunk in client.stream(messages, ...):
        writer({"type": "llm_delta", "text": chunk})
        full_text += chunk
    ...
```
Turn handler đổi `app.invoke(initial)` → lặp qua `app.astream(initial, stream_mode="custom")`, forward mỗi item thành SSE event mới `event: llm_delta`.

### B. LLM layer: thêm entry point stream cho node cần output cuối
Chỉ node **synthesize** (và bất kỳ intent node nào trả report trực tiếp cho user, ví dụ `market_brief`) cần đổi từ `generate()` sang `stream()`. Các LLM call nội bộ khác (routing, gate, sub-query decomposition) giữ `generate()` — không cần stream vì user không thấy raw output đó.

→ Cần audit lại toàn bộ chỗ gọi `client.generate()` trong `agents/graph.py`, `agents/intents/*.py`, phân loại: "output cuối cho user" vs "nội bộ".

### C. SSE / turn_handler: thêm event type mới
`tracing.py` đang emit `llm`/`tool`/`gate` (start/end, không phải delta). Thêm nhánh mới song song, không đụng event cũ:
- `event: llm_delta` → `{"text": chunk}` — cho token thật.
- Giữ `event: llm` (start/end) để tool-chip UI vẫn hoạt động như cũ.
- `event: done` giữ nguyên hợp đồng hiện tại (đã có test bám vào format này — `tests/test_bai31_streaming.py`, `test_graph_unified.py`, ...).

### D. Client (UI) — `ui/react/src/api.ts`, `ui/chat.py`
Thêm handler cho `llm_delta` để append chữ dần vào bong bóng chat, giữ tool chip render như hiện tại từ `tool`/`gate` event.

## Rủi ro / cần xác nhận trước khi code

1. **LangGraph version hiện tại có `get_stream_writer` / `stream_mode="custom"` không** — cần check `requirements.txt` pin version, một số bản cũ chỉ có `stream_mode="values"/"updates"`. Nếu không có, phương án B là dùng callback truyền qua `RunnableConfig`/`contextvars` (giống cách `tracing.py` đang dùng `ContextVar` cho `current_turn_start_ts`) — tự bơm chunk vào queue thay vì dùng writer built-in.
2. Node nào khác ngoài `synthesize` trả text trực tiếp cho user? (`market_brief`, các intent thin-wrapper gọi `run()` bên trong `agents/intents/*.py`) — cần grep hết để không bỏ sót.
3. Checkpointer (`g.compile(checkpointer=checkpointer)`) — `astream` với checkpointer có thay đổi hành vi resume/interrupt không, cần test riêng.
4. Disconnect giữa chừng (bài học từ bài 31: không save_turn nếu client cancel) — áp dụng lại logic đó cho graph turn.

## Việc cần làm (theo thứ tự)

1. Xác nhận version LangGraph + khả năng `stream_mode="custom"` (đọc `requirements.txt`, chạy thử 1 script nhỏ).
2. Grep toàn bộ `client.generate(` trong `agents/` — lập danh sách node "user-facing" cần đổi sang `stream()`.
3. Đổi `node_synthesize` sang dùng `client.stream()` + `get_stream_writer()` (hoặc contextvar queue nếu LangGraph không hỗ trợ).
4. Đổi `memory/turn_handler.py`: `app.invoke(initial)` → `for event in app.astream(initial, stream_mode="custom"): ...` forward SSE.
5. Thêm `event: llm_delta` vào SSE contract, update `chat-context-spec.md` / `docs/graph-flow.md` nếu có mô tả contract cũ.
6. Update UI (`ui/chat.py`, `ui/react/src/api.ts`) nhận `llm_delta`.
7. Test: viết test mới kiểu `test_bai31_streaming.py` nhưng chạy qua graph thật (có tool call + gate), assert chunk nhỏ dần tới xuất hiện trước `event: done`, và test disconnect-mid-stream không save turn.
8. Chạy thật (không mock) 1 turn qua market_brief / synthesize, xem output SSE bằng `curl --no-buffer`, xác nhận chữ chảy dần đúng thứ tự, không bị đảo hoặc trùng.
