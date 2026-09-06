# Báo cáo cuối kỳ — Luồng LangGraph của hệ thống phân tích chứng khoán Việt Nam

> Trọng tâm báo cáo: **Luồng LangGraph** — cách một câu hỏi ngôn ngữ tự nhiên chảy qua graph trạng thái để thành báo cáo Markdown. Tools và thách thức được trình bày ở mức hỗ trợ cho luồng. Bỏ qua cache và bộ nhớ hội thoại (memory).

---

## 1. Tổng quan luồng

Một lượt (turn) đi qua ba tầng, trong đó **LangGraph là tầng giữa** — nơi toàn bộ trạng thái được điều phối:

```
user query
   │
   ▼
┌────────────────────────────────────────────────────────────┐
│  TẦNG 1 — conversation_router (LLM-on-top, 1 lần gọi LLM)   │
│  Ép chọn tool: needs_agent_run / direct_reply / out_of_scope│
│  / decompose (multi-intent). Kết quả đổ vào AgentState.     │
└────────────────────────────────────────────────────────────┘
   │ type="agent"
   ▼
┌────────────────────────────────────────────────────────────┐
│  TẦNG 2 — agents/graph.py (LANGGRAPH — trọng tâm báo cáo)   │
│  classify → clarify → decompose/fast-path → run_subqueries  │
│  → synthesize → critique → finalize                         │
└────────────────────────────────────────────────────────────┘
   │
   ▼
┌────────────────────────────────────────────────────────────┐
│  TẦNG 3 — intent modules (gather_data / run) → tools/*      │
│  Lấy dữ liệu thực: giá, chỉ báo, định giá, tin, vĩ mô, RAG  │
└────────────────────────────────────────────────────────────┘
```

Ba nguyên tắc chi phối thiết kế graph:

1. **State chỉ chứa text/đường dẫn, không chứa DataFrame** — để checkpoint tuần tự hoá được và không phình.
2. **Điều hướng là code thuần, nội dung là LLM** — node nào tạo nội dung mới gọi model; node điều hướng (`check_conversation`, `_route_after_clarify`, `route_after_subqueries`, `route_after_critique`, `_check_approval_decision`) đều là `if/else`.
3. **Mọi vòng lặp có giới hạn** — replan ≤ 1 lần, critique ≤ `MAX_CRITIQUE`, tổng ngân sách turn giới hạn bởi `MAX_TURN_LLM_CALLS` + `MAX_TURN_SECONDS`.

---

## 2. Xây dựng graph

`agents/graph.py build_graph(checkpointer=None, human_approval=False)` tạo `StateGraph(AgentState)`, đăng ký node, nối edge, rồi `g.compile(checkpointer=checkpointer)`.

```python
g = StateGraph(AgentState)
g.add_node("classify_node", classify_node)
g.add_node("clarify_node", clarify_node)
g.add_node("node_market_brief", node_market_brief)
g.add_node("node_out_of_scope", node_out_of_scope)
g.add_node("build_single_subtask_node", build_single_subtask_node)
g.add_node("decompose_node", decompose_node)
g.add_node("run_subqueries_node", run_subqueries_node)
g.add_node("synthesize_final", synthesize_final)
g.add_node("critique_report_node", critique_report_node)
g.add_node("finalize_node", finalize_node)
# human_approval → thêm node "request_approval"
g.set_entry_point("classify_node")
# ... conditional_edges + edge ...
return g.compile(checkpointer=checkpointer)
```

**Điểm vào** là `classify_node`. `checkpointer` là bắt buộc khi dùng `clarify_node` (interrupt) và `human_approval` — production truyền `PostgresCheckpointer()`.

---

## 3. Sơ đồ luồng đầy đủ

```
conversation ────────────────► END
out_of_scope ──► node_out_of_scope ──► END

classify_node ──► clarify_node ──► route
                                    ├─ market_brief ──► node_market_brief ─┐
                                    ├─ simple ──► build_single_subtask ────┤
                                    └─ decompose ──► decompose_node ───────┘
                                                           │
                                                           ▼
                                                  run_subqueries_node
                                                           │
                                       empty > 50%? ── replan ──► decompose_node (≤1 lần)
                                                           │
                                                           ▼
                                                  synthesize_final ◄────┐
                                                           │              │ fail (≤ MAX_CRITIQUE)
                                                           ▼              │
                                                  critique_report_node ─┘
                                                           │ pass
                                                           ▼
                                                  finalize_node ──► END
```

---

## 4. Bảng node — vai trò và LLM call

| Node | Nhiệm vụ | Gọi LLM? |
|------|----------|----------|
| `classify_node` | Gán `intent` + `ticker` + `time_context`; set-up trace Langfuse. Nếu router đã phân loại sẵn (`intent` có sẵn và ≠ `conversation`) thì dùng luôn, không gọi lại. | Không (production) |
| `clarify_node` | `interrupt()` dừng graph hỏi người dùng khi thiếu intent/ticker; resume thì gộp câu trả lời rồi phân loại lại. | Có (phân loại lại) |
| `node_out_of_scope` | Từ chối crypto/cổ phiếu ngoại/ngoại hối không chân VND bằng câu cố định `OUT_OF_SCOPE_REPLY`. | Không |
| `node_market_brief` | Gọi `build_brief_graph()` (graph con riêng) để sinh market brief. | Có (graph con) |
| `decompose_node` | Phân rã query gốc thành ≤4 sub-task, mỗi sub-task có intent riêng. | Có |
| `build_single_subtask_node` | Fast-path: gói intent+ticker đã biết thành 1 sub-task, bỏ qua decompose. | Không |
| `run_subqueries_node` | Chạy `gather_data` cho từng sub-task; fan-out song song cho intent fan-out. | **Không** |
| `synthesize_final` | Gộp dữ liệu đã gather thành báo cáo cuối; stream token ra SSE. | Có |
| `critique_report_node` | LLM tự chấm báo cáo theo checklist; trả `{pass, feedback}`. | Có |
| `finalize_node` | Đóng trace span của turn. | Không |

## 5. Bảng edge — điều hướng

**Conditional edges** (hàm trả chuỗi key → node đích):

| Hàm | Keys → đích |
|------|-------------|
| `check_conversation` | `skip → END`, `out_of_scope → node_out_of_scope`, `verify → clarify_node` |
| `_route_after_clarify` | `market_brief → node_market_brief`, `simple → build_single_subtask_node`, `decompose → decompose_node`, `conversation → finalize_node`, `out_of_scope → node_out_of_scope` |
| `route_after_subqueries` | `replan → decompose_node`, `synthesize → (request_approval | synthesize_final)` |
| `_check_approval_decision` (human_approval) | `end → END`, `synthesize_final → synthesize_final` |
| `route_after_critique` | `save → finalize_node`, `retry → synthesize_final` |

**Static edges**: `node_market_brief → finalize_node`; `node_out_of_scope → END`; `build_single_subtask_node → run_subqueries_node`; `decompose_node → run_subqueries_node`; `synthesize_final → critique_report_node`; `finalize_node → END`.

---

## 6. Cơ chế state — channel và merge

`AgentState` là `TypedDict(total=False)`. Mỗi node trả **một dict con** (chỉ các trường nó thay đổi); LangGraph merge dict này vào state trước khi sang node kế.

Chi tiết đáng chú ý: với các trường **danh sách tích luỹ** (`history`, `report_candidates`, `critique_results`), code không dùng channel reducer mà làm **read-modify-write** ngay trong node:

```python
return {
    "history": state.get("history", []) + [{...}],          # nối thủ công
    "report_candidates": state.get("report_candidates", []) + [report],
    "llm_calls": state.get("llm_calls", 0) + 1,
}
```

Điều này giữ đúng ngữ nghĩa "append" mà không cần khai báo reducer riêng, và dễ đọc hơn khi trace.

Các trường then chốt:

- **Routing**: `intent`, `ticker`, `tickers` (list đầy đủ cho so sánh), `sector`, `original_query` (verbatim — để synthesize trả lời đúng câu hỏi gốc, không phải query đã bị router viết lại).
- **Decompose/replan**: `sub_tasks`, `sub_results`, `sub_results_empty_ratio`, `replan_attempted`, `replan_note`.
- **Critique**: `critique_pass`, `critique_feedback`, `critique_attempts`, `report_candidates`, `critique_results`.
- **Thời gian**: `time_context` (`{anchor, start_date, end_date, explicit}`).
- **Ngân sách**: `llm_calls`, `turn_started_at`.

---

## 7. Chi tiết từng node

### 7.1 `classify_node`

Trong production, router đã phân loại sẵn, nên node này chỉ "đóng gói": nếu `intent` có sẵn và ≠ `conversation`, tạo `_R(intent, ticker, reason)` từ state, **không gọi LLM**. Ngược lại (gọi trực tiếp graph, test, hoặc resume) mới gọi `classify_hybrid`.

Node cũng đặt `current_request_id` và gọi `get_tracer().turn_start(...)` để mở span trace, rồi cập nhật metadata Langfuse (session_id, user_id, intent, ticker).

`time_context` ưu tiên giá trị router đã truyền (qua `stream_turn`), nếu không có mới gọi `_resolve_time_context` (chỉ cho đường gọi trực tiếp graph).

### 7.2 `clarify_node` — interrupt

Gọi `detect_ambiguity(route, query)`: nếu intent/ticker thiếu, `build_clarification_message` tạo câu hỏi, rồi `interrupt(question)` **treo graph**. Khi người dùng trả lời, graph resume tại đúng điểm này với `answer`.

Sau resume:
- `merge_with_pending(pending, answer)` gộp câu trả lời vào query.
- `classify_hybrid(merged_query)` phân loại lại.
- Nếu phân loại lại ra `conversation` (người dùng nói "bỏ qua"), trả một câu xác nhận để turn không rỗng.
- Trích ticker/sector mới; **reset `turn_started_at=time.time()` và `llm_calls=0`** (xem §9.3).

### 7.3 `decompose_node`

Gọi `rag/multi_query.py generate_sub_tasks(decompose_query, n=4)` — một lần LLM call trả ≤4 dòng `intent | câu hỏi con`. Với mỗi sub-task, trích ticker từ câu hỏi con (hoặc fallback về ticker/query gốc), giới hạn `MAX_FANOUT_TICKERS=5`.

Khi `replan_note` có sẵn (lượt re-plan), node gấp note thất bại vào prompt để sinh bộ câu hỏi **khác góc nhìn**, và đặt `replan_attempted=True` để loop chỉ chạy một lần.

### 7.4 `build_single_subtask_node` — fast-path

Gói `intent + tickers + query` thành một sub-task duy nhất, bỏ qua decompose. Ưu tiên `tickers` đã lưu trong state (router mang end-to-end); nếu không có (gọi trực tiếp graph) thì re-extract, và khi query bị LLM-expanded làm rơi ticker thứ 2/3, dùng `original_query` (verbatim) thay thế.

### 7.5 `run_subqueries_node` — gather, không LLM

Với mỗi sub-task:
1. Tra `_GATHER_MAP` lấy hàm `gather_data` theo intent. Intent lạ → log + fallback về `macro_sector`.
2. Intent ∈ `_FANOUT_INTENTS` (`price_action`, `technical_analysis`, `investment_case`, `breakout_scan`) → `_gather_tickers` fan-out song song; còn lại chạy tuần tự với ticker chính.
3. `_safe_gather` bọc lỗi thành chuỗi inline `[TICKER — lỗi: ...]`, không bao giờ raise.
4. `_is_empty_result` đánh giá "rỗng" trên **dữ liệu thô** (không để nhãn `[INTENT — TICKER]` làm sai lệch phán đoán).
5. Gói kết quả thành `[INTENT — TICKERS]\n{câu hỏi}\n{dữ liệu}` vào `sub_results`.

Sau cùng tính `empty_ratio = (total - usable)/total`. Nếu `> 0.5` và chưa replan, điền `replan_note` kể tên các sub-task thất bại.

### 7.6 `synthesize_final`

- `_strip_subresult_labels` bỏ nhãn ngoài `[INTENT — TICKER]` nhưng **giữ nhãn nguồn bên trong** `[KỸ THUẬT HPG]`, `[CƠ BẢN & ĐỊNH GIÁ HPG]` — LLM cần chúng để biết dữ liệu đến từ đâu.
- Dùng `original_query` (verbatim) thay vì query đã viết lại, để trả lời đúng câu hỏi người dùng.
- `n_blocks > 1` → bật quy tắc "tổng hợp đầy đủ mọi khối"; một khối → trả lời trực tiếp.
- Đọc `STRICT_NEUTRAL` để cấm khuyến nghị mua/bán.
- **Stream** token-by-token qua `emit_llm_delta` (chỉ lượt đầu, không phải retry); fallback `generate()` nếu stream lỗi/rỗng.
- `critique_feedback` (nếu retry) được gấp vào prompt.

### 7.7 `critique_report_node`

LLM chấm báo cáo theo checklist (mọi số liệu có nguồn, không truncation, đúng trọng tâm), trả JSON `{"pass": bool, "feedback": str}`. Parse bằng regex `\{.*\}` sau khi cắt `<think>`; mọi lỗi → `pass=True` (critique hỏng không chặn turn). Khi retry cạn mà vẫn fail, giữ bản nháp **tốt nhất** từ `report_candidates` + `critique_results`.

### 7.8 `finalize_node`

Đóng trace span (`turn_end`) với intent/ticker/report_len; không gọi LLM.

---

## 8. Các hàm điều hướng (logic thuần)

- `check_conversation`: intent `conversation` → `skip` (END, không cần graph); `out_of_scope` → từ chối; còn lại `verify` (vào clarify).
- `_route_after_clarify`: intent phức hợp (`market_brief`, `investment_case`, `macro_sector`) → `decompose`; intent khác **và có ticker** → `simple` (fast-path); intent khác không ticker → `decompose`; `conversation`/`out_of_scope` → thoát.
- `route_after_subqueries`: hết ngân sách → `synthesize` ngay; `empty_ratio > 0.5` và chưa replan → `replan`; còn lại `synthesize`.
- `route_after_critique`: hết ngân sách → `save`; `critique_pass` → `save`; chưa quá `MAX_CRITIQUE` → `retry`; quá → `save`.
- `_check_approval_decision` (human_approval): `error == "rejected_by_user"` → `end`; ngược lại `synthesize_final`.

---

## 9. Interrupt & resume — vòng đời

### 9.1 Cách graph được gọi (production)

`memory/turn_handler.py stream_turn` là entry thực sự:

```python
checkpointer = PostgresCheckpointer()
app = build_graph(checkpointer=checkpointer)
thread_config = {"configurable": {"thread_id": conversation_id}}
```

- `thread_id` = `conversation_id` → mỗi cuộc hội thoại là một luồng checkpoint riêng.
- Khi router trả `type="agent"`, `app.invoke(agent_state, thread_config)` chạy graph trong **daemon thread** với `current_observer` được set, để `emit_llm_delta` từ `synthesize_final` trở thành SSE token theo thời gian thực.

### 9.2 Resume interrupt

Khi `clarify_node` đã gọi `interrupt()`, graph dừng. Lượt sau, `stream_turn` phát hiện trạng thái interrupted qua `_read_prior` và resume bằng:

```python
app.invoke(Command(resume=user_message), thread_config)
```

`Command(resume=...)` cung cấp câu trả lời cho lời gọi `interrupt()` đang treo. Graph tiếp tục từ `clarify_node` mà **không chạy lại các node trước** (nhờ checkpoint).

### 9.3 Stale interrupt — bug tinh tế

Resume khôi phục state **từ thời điểm interrupt tạo**, có thể cách hàng phút/giờ. Hai hệ quả đã được xử lý:

1. **Ngân sách turn sai**: state cũ mang `turn_started_at`/`llm_calls` cũ → turn mới bị coi là đã hết ngân sách, âm thầm bỏ qua replan/critique-retry. Fix: `clarify_node` trả `turn_started_at=time.time()` + `llm_calls=0` khi resume.
2. **Interrupt quá hạn**: nếu interrupt tồn tại lâu hơn `INTERRUPT_STALE_SECONDS`, `stream_turn` **bỏ rơi** nó bằng cách tạo `thread_id` mới `{conversation_id}:stale:{ts}` — turn này route và chạy bình thường thay vì bị interrupt cũ "nuốt".

### 9.4 Human-approval (tùy chọn, bài 27)

`build_graph(human_approval=True)` chèn `request_approval` giữa `run_subqueries` và `synthesize`. Node này gọi `interrupt(proposal)` (proposal chứa ticker/query/preview dữ liệu); người duyệt trả `True`/`False`, `_check_approval_decision` dẫn tới `synthesize_final` hoặc `END`.

---

## 10. Ngân sách turn — chống loop vô hạn

`_llm_budget_exceeded(state)` chặn khi `llm_calls ≥ MAX_TURN_LLM_CALLS` (8) hoặc `time.time() - turn_started_at > MAX_TURN_SECONDS` (180s). Nó được gọi ở **mọi điểm rẽ nhánh có thể quay vòng** (`route_after_subqueries`, `route_after_critique`) — hết ngân sách thì đi thẳng đến `synthesize`/`save`, không thêm LLM call. Ba vòng lặp trong graph:

| Vòng lặp | Giới hạn | Điều kiện |
|----------|----------|-----------|
| Replan | ≤ 1 lần | `empty_ratio > 0.5` và `replan_attempted == False` |
| Critique retry | ≤ `MAX_CRITIQUE` (1) | `critique_pass == False` và `critique_attempts ≤ MAX_CRITIQUE` |
| Ngân sách turn | 8 LLM call / 180s | chặn mọi vòng lặp khi hết |

---

## 11. Streaming từ graph ra SSE

`tracing.py` có `emit_llm_delta(text)` đẩy token tới `current_observer`. Trong `stream_turn`, observer này là một closure đặt sự kiện vào `asyncio.Queue`:

```python
def _observer(kind, data):
    loop.call_soon_threadsafe(stream_q.put_nowait, (kind, data))
```

- `synthesize_final` gọi `emit_llm_delta(chunk)` mỗi token → observer → queue → `yield _sse_chunk(...)`.
- Node khác phát sự kiện `tool` / `gate` qua tracer → observer → `yield _sse_tool(...)` / `yield _sse_gate(...)`.
- Heartbeat `: heartbeat` mỗi 15s giữ kết nối.
- Chỉ **lượt đầu** stream; critique-retry dùng `generate()` để SSE không nhận `draft1 + draft2` nối nhau (mà `assistant_reply` chỉ lưu bản cuối).
- `CancelledError` (client ngắt) → turn **không lưu**.

Sau `invoke`, `stream_turn` gọi `app.get_state(thread_config)` và kiểm tra `after_state.tasks[0].interrupts` — nếu graph đang chờ clarify thì stream chính câu hỏi clarify ra client.

---

## 12. Walkthrough end-to-end: "đánh giá HPG hôm nay"

Bảng chuyển trạng thái theo node:

| Bước | Node | Hành động | State thay đổi |
|------|------|-----------|----------------|
| 1 | *(router, ngoài graph)* | LLM gọi `needs_agent_run(intent=investment_case, ticker=HPG, time_context={start:null, explicit:false})` | `intent`, `ticker`, `query`, `time_context`, `original_query` |
| 2 | `classify_node` | Dùng kết quả router, mở trace | giữ nguyên |
| 3 | `clarify_node` | `detect_ambiguity` → `None` (đủ intent+ticker) → đi tiếp | — |
| 4 | `_route_after_clarify` | `investment_case ∈ _COMPLEX_INTENTS` → `decompose` | — |
| 5 | `decompose_node` | `generate_sub_tasks(n=4)` → 4 sub-task (price_action / technical_analysis / valuation / news_sentiment) | `sub_tasks`, `llm_calls+1` |
| 6 | `run_subqueries_node` | Chạy 4 `gather_data`; 2 intent fan-out song song | `sub_results` (4 khối dữ liệu), `sub_results_empty_ratio` |
| 7 | `route_after_subqueries` | `empty_ratio ≤ 0.5` → `synthesize` | — |
| 8 | `synthesize_final` | `_strip_subresult_labels`, 1 LLM call tổng hợp, stream ra SSE | `report`, `llm_calls+1`, `history+=[...]` |
| 9 | `critique_report_node` | LLM chấm checklist → `pass=True` | `critique_pass`, `critique_results`, `llm_calls+1` |
| 10 | `route_after_critique` | `pass` → `save` | — |
| 11 | `finalize_node` | Đóng trace | — |
| 12 | *(ngoài graph)* | `stream_turn` lấy `report`, lưu turn | — |

Trường hợp `empty_ratio > 0.5`: bước 7 → `replan` → quay `decompose_node` (bước 5') với `replan_note` → `run_subqueries` (bước 6') → `synthesize`.

---

## 13. Tools — vai trò hỗ trợ luồng

`run_subqueries_node` và các intent module gọi tầng tools. Hai khái niệm nền tảng:

### 13.1 Hợp đồng `ToolResult`

Mọi tool trả `ToolResult(status, data, message)`, **không raise, không trả list rỗng trần**. `status ∈ {ok, no_data, invalid_input, upstream_error, rate_limited}`; `message` hướng dẫn bước tiếp. Nhờ vậy `_safe_gather` chỉ cần bọc `try/except` là đủ — lỗi tool không bao giờ làm sập node.

### 13.2 Tầng provider

`PriceProvider` (ABC) + các hiện thực với chuỗi fallback cho cổ phiếu VN: **Fireant → VCI → TCBS**; chỉ số VN → `SsiIndexProvider` (VCI OHLCChart); mã ngoại → `YFinanceProvider`. `_detect_provider` quyết định theo hình thái ticker và universe `securities`.

### 13.3 Intent module — `gather_data` / `run`

Mỗi intent tách hai hàm: `gather_data` (thuần Python, trả dữ liệu text cho graph) và `run` (có LLM, theo market-brief pattern: Python tính số → LLM viết slot → Python lắp Markdown). Đây chính là thứ làm `run_subqueries_node` "không cần LLM" — dữ liệu đã được chuẩn bị sẵn dưới dạng text.

---

## 14. Thách thức chính trong luồng

### 14.1 Rò rỉ quá trình suy nghĩ (thinking leak)

DeepSeek nhét `<think>...</think>`, dòng lập luận, hoặc lặp system prompt vào output. Xử lý nhiều tầng: client `strip_thinking_output`, `llm/utils.py strip_thinking` (bảo vệ `<report>`, cắt think block, lọc dòng lập luận, chặn system-leak, dedup H1), và intent `extract_report`/`extract_slot` lấy label cuối.

### 14.2 Bịa số / suy diễn lại thứ hạng

`fundamentals.py` tính sẵn xếp hạng + so sánh trong Python rồi chèn câu sự kiện vào prompt; system cấm "tự tính lại". Node critique kiểm "mọi số liệu có nguồn".

### 14.3 Trôi sub-query / JSON cụt / ảo giác SQL

- Sub-query drift → prompt ràng buộc + replan một lần.
- Tool-call JSON cụt → `OpenAIClient` cắt về `}` cuối rồi parse lại.
- LLM gọi bảng SQL không tồn tại → `_sql_answer` bắt `SecurityError`, nối `[CORRECTION: ...]`, retry ≤3.

### 14.4 Fan-out treo cấp số nhân

`_gather_tickers` dùng **một deadline chia sẻ** cho toàn bộ future (mỗi future chỉ nhận ngân sách còn lại) + `shutdown(wait=False, cancel_futures=True)`, tránh N×30s nối tiếp.

### 14.5 Độ tươi dữ liệu & đơn vị

DB-first + `_is_db_fresh` (so ngày giao dịch gần nhất, bỏ lễ VN) → fallback live. Khối ngoại chuẩn hoá `/1e9` (tỷ), indicator nhận `currency` để tránh nhầm VND/USD.

### 14.6 Stream vs lưu reply lệch nhau

Chỉ lượt đầu stream; retry dùng `generate()`, tránh SSE nối `draft1+draft2`.

### 14.7 Từ chối ngoài phạm vi

Router có tool `out_of_scope`; graph có `node_out_of_scope` trả câu cố định — không gather, không LLM.

---

## 15. Kết luận

Luồng LangGraph là trái tim của hệ thống: nó biến một câu hỏi tự do thành một chuỗi bước **có cấu trúc, có giới hạn, có thể checkpoint/resume**. Ba quyết định thiết kế quan trọng nhất:

1. **Điều hướng bằng code thuần** — mọi nhánh rẽ (routing, replan, critique) là `if/else`; chỉ node sinh nội dung gọi LLM. Chi phí và độ trễ dự đoán được.
2. **State chỉ chứa text** — để checkpoint tuần tự hoá được, và để `run_subqueries` có thể gather dữ liệu dạng text mà không cần LLM.
3. **Mọi vòng lặp có trần** — replan ≤1, critique ≤`MAX_CRITIQUE`, ngân sách turn 8 call/180s, đảm bảo turn "bệnh hoạn" không đốt tiền vô hạn.

Interrupt/checkpoint (clarify, human-approval) cùng cơ chế streaming qua observer là hai điểm kết nối graph với thế giới ngoài (người dùng và SSE), và cũng là nơi phát sinh những bug tinh tế nhất (stale checkpoint, reset ngân sách khi resume, stream lệch với lưu).
