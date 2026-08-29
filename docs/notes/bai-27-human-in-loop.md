# Bài 27 · Tạm dừng chờ người — Kết quả thực

**Ngày:** 29/08/2026  
**Stack:** LangGraph 1.2.11 · PostgresCheckpointer (tự xây) · FastAPI · psycopg2

---

## Kiến trúc

```
collect → analyze_technical → assess_risk → request_approval → synthesize
                                                    ↑
                                          interrupt() ở đây
```

- `interrupt()` của LangGraph tạm dừng graph, raises `GraphInterrupt`
- LangGraph lưu toàn bộ checkpoint vào Postgres qua `PostgresCheckpointer`
- Runner cũng lưu `AgentState` snapshot vào `agent_sessions` (JSONB) để API đọc

## File tạo mới

| File | Mục đích |
|------|---------|
| `migrations/027_agent_sessions.sql` | `agent_sessions` + 3 bảng LangGraph checkpoint |
| `agents/checkpointer.py` | `PostgresCheckpointer` (BaseCheckpointSaver) + `save_checkpoint` / `load_checkpoint` |
| `agents/graph_interactive.py` | Graph với `request_approval` node dùng `interrupt()` |
| `agents/run_interactive.py` | CLI runner, dừng sau interrupt, in session_id |
| `api/sessions.py` | 3 endpoint: GET /pending, POST /approve, POST /reject |
| `api/main.py` | FastAPI app + background task expire mỗi 60s |

## Schema Postgres

```sql
agent_sessions    -- session_id, ticker, state JSONB, status, expires_at, audit_log JSONB
lg_checkpoints    -- thread_id, checkpoint_ns, checkpoint_id, checkpoint_data BYTEA
lg_checkpoint_blobs  -- channel value blobs (BYTEA)
lg_checkpoint_writes -- pending writes (BYTEA)
```

**Lý do không nhét DataFrame vào state:** bài 22 cảnh báo điều này — khi serialize sang JSONB sẽ fail. State chỉ giữ `price_data_path` (path tới CSV).

## Kết quả chạy thực

### VCB — test chính "sống qua restart"

```
ticker=VCB  session_id=ea2aef4b...
Risk: OK (14-session std=1.02%)
RSI(14)=58.7 · MACD histogram=+201.40 · MA20/MA50 bullish · ADX=21.7 (yếu)
```

Resume từ process mới (giả lập restart):
- `lg_checkpoints rows for session: 5` — checkpoint đã trong Postgres
- `app.invoke(Command(resume=True), config={"thread_id": session_id})` → synthesize chạy đúng chỗ
- Báo cáo: **Trung tính** (MACD tăng nhưng ADX yếu + volume thấp hơn TB 22%)

### FPT

```
Risk: OK (14-session std=1.58%)
Kết luận: Tích cực
RSI=62.8 · MACD histogram=+372.92 · volume cao hơn TB 44% · cổ tức FPT Online 100%
```

### HPG

```
Risk: OK (14-session std=1.55%)
Kết luận: Tích cực  
MACD cắt lên signal · giá dưới MA50 nhưng histogram dương
```

## Kiểm tra "Xong khi"

| Tiêu chí | Kết quả |
|----------|---------|
| Chạy tới interrupt → kill process → session còn trong Postgres → approve → synthesize tiếp đúng chỗ | PASSED (VCB) |
| 2 phiên song song (FPT + VCB) không lẫn state | PASSED |
| Approve sau 6 phút → HTTP 410 | PASSED |

## Lệnh chạy

```bash
# Dừng chờ người (API)
python -m agents.run_interactive FPT

# End-to-end test (auto-approve)
python -m agents.run_interactive FPT --approve

# Start API server
python -m uvicorn api.main:app --reload --port 8027
# GET  http://localhost:8027/sessions/pending
# POST http://localhost:8027/sessions/{id}/approve
# POST http://localhost:8027/sessions/{id}/reject
```

## Quan sát kỹ thuật

**PostgresCheckpointer phức tạp hơn tưởng:**
- LangGraph checkpoint không phải đơn giản là serialize state dict
- Có 3 lớp: checkpoint metadata, channel blobs (binary per version), pending writes
- Dùng `serde.dumps_typed()` → `(type_str, bytes)` → pack thành BYTEA
- Nếu dùng `InMemorySaver` thay thế: mất hết khi server restart — đây là điểm bài dạy

**TTL 5 phút là đủ ngắn để test, đủ dài để human review:**
- Background task `expire_old_sessions()` chạy mỗi 60s
- `_get_pending()` double-check `expires_at < NOW()` ngay cả khi sweeper chưa kịp chạy

**Cái bẫy đã gặp:** `cur.rowcount` cho SELECT trong psycopg2 không đáng tin — fix dùng `fetchone() is not None`.
