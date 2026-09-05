# Plan: cải thiện routing — `agents/graph.py` + `agents/conversation_router.py`

Ngày review: 2026-09-05. Phạm vi: chỉ routing (không đụng logic intent modules).

Nguồn kiểm chứng: đọc trực tiếp `agents/graph.py` (1061 dòng), `agents/conversation_router.py`
(208 dòng), `agents/classifier.py`, `memory/turn_handler.py`, `core/cache.py`,
`agents/intents/fundamentals.py`; grep toàn repo để xác nhận caller.

---

## 0. Luồng routing hiện tại (3 tầng)

```
user_message
  └─ memory/turn_handler.stream_turn
       ├─ nếu graph đang interrupt (prior_state.next) → Command(resume=msg), BỎ QUA router
       └─ llm_route()                        [LLM call #1 — tool: needs_agent_run | direct_reply]
            ├─ type="text"  → stream trả lời trực tiếp
            └─ type="agent" → app.invoke(intent, ticker, query, original_query)
                 └─ graph.classify_node       (pre-classified → KHÔNG gọi LLM lại)
                      └─ check_conversation → check_cache_node → clarify_node
                           └─ _route_after_clarify
                                ├─ market_brief → node_market_brief
                                ├─ simple       → build_single_subtask_node
                                └─ decompose    → decompose_node   [LLM call #2]
                                     → run_subqueries_node (gather, no LLM)
                                     → synthesize_final [LLM #3] → critique [LLM #4]
```

---

## 1. Phát hiện — đã kiểm chứng

### 1.1 ~400 dòng node/router chết trong `graph.py` (CONFIRMED)

`build_graph()` (graph.py:997-1006) chỉ đăng ký 10 node. Các hàm sau **không** được
`add_node` và không có caller nào ngoài file (grep toàn repo — chỉ còn tên trong chuỗi
print/docstring của `agents/run.py:61`, `agents/run_interactive.py:9,48`):

| Chết | Dòng |
|---|---|
| `route_question`, `pick_branch`, `decide_next` | 232, 239, 251 |
| `_get_bm25`, `fusion_search`, `grade_or_critique`, `run_web_search` | 263-315 |
| `collect`, `analyze_technical`, `assess_risk`, `synthesize` | 317-472 |
| `node_price_action` … `node_screening`, `node_breakout_scan`, `node_rag_qa` | 475-533 |
| Hằng: `_VOLATILITY_THRESHOLD`, `_RAG_COLLECTION`, `_EMBED_MODEL`, `MAX_ITER`, `_bm25_cache`, `_INTENT_NODES`, `_KNOWLEDGE_KEYWORDS` | 48-79 |

Hệ quả: đọc file bị nhầm là còn tồn tại 2 kiến trúc routing song song
(`pick_branch` theo keyword vs `_route_after_clarify` theo intent). `state.py:39`
còn field `route` mô tả "set by route_question" — không ai set.

**Việc cần làm:** xoá toàn bộ khối trên; sửa docstring `agents/run.py`,
`agents/run_interactive.py`; bỏ field `route` khỏi `AgentState` nếu không còn ai đọc.
`_COMPLEX_INTENTS`, `_FANOUT_INTENTS`, `_MARKET_TICKERS` GIỮ (còn dùng).

### 1.2 `intent="conversation"` sau clarify không có đường thoát (BUG)

`clarify_node` (graph.py:186-229) sau khi merge câu trả lời user thì gọi
`classify_hybrid` lại → có thể trả `conversation`. `_route_after_clarify`
(graph.py:904-928) không xử lý case này: nhánh `market_brief`? không → nhánh `simple`
yêu cầu `intent not in _COMPLEX_INTENTS and intent != "conversation" and ticker` → false
→ rơi xuống `decompose`. Decompose với parent_intent="conversation" →
`_GATHER_MAP["conversation"]` trả `""` (graph.py:657) → mọi sub_result rỗng →
replan 1 lần → vẫn rỗng → `synthesize_final` chạy với context rỗng → báo cáo bịa.

**Fix:** thêm nhánh `"conversation" → END` (hoặc → `cache_save_node` để `turn_end` vẫn
chạy) trong `_route_after_clarify` + `build_graph`.

### 1.3 Cache key va chạm cho follow-up "phân tích sâu hơn" (BUG)

`check_cache_node` (graph.py:150-178) cố ý dùng `original_query` (verbatim) làm question.
`make_cache_key` (core/cache.py:118-142) → `stable_ticker = _extract_all_tickers(question)`,
và với `intent="macro_sector"` thì `scope = hash(normalize(question))`.

Follow-up tiếp diễn về **ngành/chỉ số** (router giữ `intent=macro_sector`, `ticker=""`
theo mô tả tool ở conversation_router.py:29-34) có `original_query="phân tích sâu hơn"`
→ key = (tenant, macro_sector, ticker="", scope=hash("phân tích sâu hơn")).
Key này **giống nhau giữa mọi ngành và mọi conversation** (cache là cross-conversation,
core/cache.py:18) → hội thoại về ngành thép có thể nhận lại báo cáo ngân hàng đã cache.

**Fix (chọn 1):**
- (A) Trong `check_cache_node`: dùng `original_query` chỉ khi nó tự chứa chủ thể
  (có ticker/tên ngành); ngược lại dùng `state["query"]` (câu self-contained router viết lại).
- (B) Router trả thêm field `subject` (ticker hoặc sector đã resolve); graph ghép
  `subject` vào question trước khi hash.

Khuyến nghị (A), điều kiện phải tường minh (có ticker trong universe hoặc khớp danh sách
sector) — **không** dùng heuristic độ dài. Phụ thuộc 1.4.

### 1.4 `_extract_query_tickers` false-positive từ từ tiếng Việt không dấu

graph.py:559-578: `re.findall(r"\b[A-Z]{2,5}\b", query.upper())` — vì `.upper()` áp lên
toàn câu, **mọi** từ không dấu 2-5 ký tự đều thành ứng viên; lọc duy nhất là universe
`core.tickers.get_tickers()`. Các từ tiếng Việt không dấu trùng mã niêm yết
(vd "tin", "ban", "cho", "nam", "hai", "sao", "cap", "hop") sẽ được nhận là ticker →
fan-out sai mã ở `decompose_node` (graph.py:590-597) và `build_single_subtask_node`
(graph.py:944).

Đã kiểm chứng: `core/cache.py:88-96` có `_TICKER_STOPWORDS` cho đúng vấn đề này →
repo đang có **hai** hàm extract ticker song song, một chỗ có stopword, một chỗ không.

**Fix:** hợp nhất về một helper duy nhất (đưa `_extract_all_tickers` + `_TICKER_STOPWORDS`
sang `core/tickers.py`, hai nơi cùng gọi); ưu tiên token **vốn hoá sẵn trong câu gốc**
(match trước khi `.upper()`), chỉ fallback case-insensitive khi câu không có token in hoa.

### 1.5 `run_subqueries_node` fetch tuần tự (HIỆU NĂNG)

graph.py:674-736: vòng `for task in sub_tasks` tuần tự, và trong fan-out lại
`for t in tickers` tuần tự. Worst case 4 sub-task × 5 ticker = 20 lượt gọi mạng nối tiếp.
`ThreadPoolExecutor` đã import ở graph.py:33 nhưng chỉ dùng trong `collect()` — hàm đã chết.

**Fix:** `ThreadPoolExecutor` cho sub-task, và cho fan-out ticker bên trong; thêm timeout
per-task để một nguồn treo không giữ cả turn.

### 1.6 Intent lạ bị nuốt im lặng

graph.py:681: `fn = gather.get(intent, lambda t, q: "")`. Intent không có trong
`_GATHER_MAP` → trả `""` → sub_result rỗng → replan → synthesize context rỗng,
**không log, không trace**.

**Fix:** log + `get_tracer().event("gate", {"node": "gather_unknown_intent", ...})`,
fallback về `macro_sector` thay vì rỗng.

### 1.7 `_FANOUT_INTENTS` thiếu `valuation` (đã có cơ chế bù — cần khoá bằng test)

graph.py:661 không chứa `valuation`. So sánh nhiều mã ("so sánh BID và CTG") đi đường
`simple` với `intent=valuation` → chỉ `tickers[0]` được truyền vào `fn`.
Đã kiểm chứng: `agents/intents/fundamentals.py:398-411` tự parse ticker từ `query`
(`_extract_tickers_from_query`) nên vẫn cross-compare được — nhưng phụ thuộc `query`
giữ đủ mã, đúng như phần bù ở graph.py:936-941.

**Việc cần làm:** không đổi `_FANOUT_INTENTS` bây giờ; thêm test khoá hành vi
"query 2 mã → fundamentals thấy cả 2". Lâu dài: chuẩn hoá signature
`gather_data(tickers: list[str], query)`.

### 1.8 Router: fail-safe đắt và sai (`market_brief`)

conversation_router.py:206 và 208: LLM lỗi (`except Exception`) hoặc `resp.text` rỗng →
`RouteResult(type="agent", intent="market_brief")`. Một câu chào trong lúc provider lỗi
sẽ kích full market brief (graph.py:505 chạy cả `build_brief_graph`).

**Fix:** fail-safe → `type="text"` với thông báo lỗi ngắn tiếng Việt; chỉ route
`market_brief` khi `classify_hybrid` thực sự trả `market_brief`.

### 1.9 Router: fallback `classify_hybrid` mất context hội thoại

3 điểm fallback (conversation_router.py:181 trong nhánh `direct_reply`, và:195 trong
nhánh "no tool call") gọi `classify_hybrid(query)` **không** truyền `messages=history`,
dù hàm có tham số đó (classifier.py:169) và `classify_node` đã truyền (graph.py:81).
Follow-up "phân tích sâu hơn" qua fallback → không resolve được ticker →
`technical_analysis` với ticker rỗng.

**Fix:** truyền `messages=history` ở mọi điểm fallback.

### 1.10 Router: ticker không được validate

conversation_router.py:171: `ticker=(inp.get("ticker") or "").strip().upper()` — không
đối chiếu universe. LLM có thể trả "NGANHANG", tên công ty, hoặc chỉ số. Ticker sai đi
thẳng vào `build_single_subtask_node` → gather fetch mã không tồn tại.

**Fix:** validate qua `core.tickers.get_tickers()` (cho phép whitelist chỉ số trong
`_MARKET_TICKERS`); không hợp lệ → `ticker=""`, để `_extract_query_tickers`/clarify xử lý.

### 1.11 Router: không có tracing

`graph.py` phát `gate` event ở clarify, decompose, fast_path, market_brief, critique.
`llm_route` **không phát event nào** — quyết định routing quan trọng nhất (agent vs text,
intent, ticker, có dùng fallback không) không có trace. Chỉ có `log.info` ở
turn_handler.py:359 cho nhánh agent; nhánh text không log gì.

**Fix:** `get_tracer().event("gate", {"node": "llm_route", "decision", "intent",
"ticker", "fallback_used"})`; thêm field `reason` (string ngắn) vào cả 2 tool schema để
debug misroute.

### 1.12 Router: `tool_calls[0]` và fallthrough ngầm

conversation_router.py:167 chỉ đọc tool call đầu tiên. Nếu `needs_agent_run` trả intent
không hợp lệ (`intent in INTENTS` fail ở:169), code **không return** mà rơi xuống khối
"No tool call" ở:191 — chạy đúng nhưng luồng ngầm, khó đọc và dễ vỡ khi sửa.

**Fix:** tách `_fallback_classify(query, history)` và gọi tường minh ở cả 3 điểm.

### 1.13 Router: 1 LLM call mọi turn, +1 khi `direct_reply`

Mỗi turn tối thiểu 1 call router. Turn xã giao ("cảm ơn") còn bị verify thêm bằng
`classify_hybrid` (conversation_router.py:178-186) → 2 call LLM cho một câu "cảm ơn".

**Fix (tuỳ chọn, đo trước):** short-circuit tiền định trước router cho 2 case rõ ràng —
(a) message chỉ một mã trong universe → agent/technical_analysis;
(b) message khớp danh sách chào/cảm ơn ngắn → text. Chỉ làm nếu đo thấy latency đáng kể.

### 1.14 Router không biết turn trước là gì (ngoài history đã bị cắt)

conversation_router.py:148-153: content assistant bị cắt còn **120 ký tự** (cố ý, tránh
LLM trả lời từ dữ liệu cũ). Nhưng follow-up tiếp diễn cần biết **chủ thể** turn trước;
120 ký tự đầu của báo cáo có thể không chứa mã/ngành, lúc đó router phải suy từ message
user cũ (giữ 800 ký tự) — không đảm bảo. Toàn bộ khối prompt dài ở
conversation_router.py:29-34 và 66-70 hiện đang bù cho thiếu hụt này bằng prompt engineering.

**Fix (đúng gốc):** truyền tường minh `last_intent` / `last_subject` vào system prompt của
router. Nguồn: checkpoint state của thread (`app.get_state`) hoặc lưu kèm turn trong Postgres.

### 1.15 Thread interrupt "mắc kẹt" nuốt turn tiếp theo

turn_handler.py:314-321: nếu `prior_state.next` truthy → **luôn** coi message mới là câu
trả lời clarification và `Command(resume=...)`, bỏ qua router hoàn toàn. Nếu một turn
trước bị interrupt rồi user bỏ ngang (đổi chủ đề, reload UI), mọi message sau đó vẫn bị
đưa vào `clarify_node`/`request_approval` cũ.

**Fix:** lưu timestamp + node đang interrupt; nếu quá hạn (vd > 10 phút) hoặc message mới
rõ ràng là câu hỏi mới (mã khác / router trả agent) → huỷ interrupt (`app.update_state`
hoặc thread_id mới) rồi route bình thường.

### 1.16 Thứ tự cache trước clarify

Graph chạy `check_cache_node` **trước** `clarify_node` (graph.py:1017-1021). Với câu mơ
hồ, cache key tính trên text mơ hồ; sau clarify `query` đã đổi nhưng `_cache_key`
**không** được tính lại → `cache_save_node` (graph.py:537) lưu báo cáo đã clarify dưới
key của câu mơ hồ → lần sau câu mơ hồ khác lại hit đúng key đó.

**Fix:** trong `clarify_node`, khi có merge → tính lại `_cache_key` (hoặc set `None` để
không cache turn đó).

### 1.17 Critique retry không giữ bản tốt nhất

`synthesize_final` retry ghi đè `report` (graph.py:801). Nếu bản retry tệ hơn, bản đầu đã
mất. `route_after_critique` (graph.py:885) chỉ đếm số lần.

**Fix:** giữ `report_candidates: list[str]`; hết lượt thì chọn bản critique pass, nếu
không có thì bản đầu tiên.

---

## 2. Thiếu (missing) — routing chưa có

1. **Không có "out-of-scope" route.** Câu hỏi tài chính nhưng ngoài phạm vi (crypto,
   chứng khoán Mỹ, forex ngoài VND) vẫn được route vào một intent VN → gather rỗng →
   báo cáo suy diễn. Cần nhánh `out_of_scope` trả lời thẳng, không chạy graph.
2. **Không có budget guard cho một turn.** `MAX_CRITIQUE=1` và `replan_attempted` chặn
   từng vòng lặp, nhưng không có cap tổng số LLM call / tổng wall-clock cho một turn.
   Worst case hiện tại: router 1 + decompose 2 (có replan) + synthesize 2 + critique 2 = 7 call.
3. **Không có eval bộ routing.** Chưa có golden set (query → intent/ticker mong đợi) chạy
   được cho `llm_route` + `_route_after_clarify`. Đây là điều kiện cần trước khi refactor
   1.14, nếu không sẽ regress âm thầm.
4. **Intent mới (`portfolio`, `alert`, `compare_period`)** — chưa xác nhận là vấn đề thực
   tế; phải thu log misroute trước, không thêm intent theo phỏng đoán.

---

## 3. Thứ tự thực hiện đề xuất

| # | Việc | Rủi ro | Ghi chú |
|---|---|---|---|
| 1 | Golden set eval routing (2.3) | thấp | làm TRƯỚC mọi refactor |
| 2 | 1.2 nhánh `conversation` sau clarify | thấp | bug thật, ~3 dòng |
| 3 | 1.9 truyền `messages=history` vào fallback classify | thấp | 3 dòng |
| 4 | 1.10 validate ticker trong router | thấp | |
| 5 | 1.8 fail-safe router không dùng `market_brief` | thấp | |
| 6 | 1.11 tracing cho `llm_route` | thấp | cần để đo các mục sau |
| 7 | 1.1 xoá dead code | thấp (sau khi có eval) | giảm ~400 dòng |
| 8 | 1.4 hợp nhất extract ticker + stopwords | trung bình | đụng cache key |
| 9 | 1.3 cache key cho follow-up tiếp diễn | trung bình | phụ thuộc #8 |
| 10 | 1.5 song song hoá `run_subqueries_node` | trung bình | win latency lớn nhất |
| 11 | 1.6, 1.16, 1.17 | trung bình | |
| 12 | 1.14 truyền `last_intent`/`last_subject` tường minh | cao | refactor state, cần eval |
| 13 | 1.15 huỷ interrupt mắc kẹt | cao | đụng checkpointer |
| 14 | 1.13 short-circuit tiền định | tuỳ chọn | chỉ khi đo thấy cần |

---

## 4. Điểm CHƯA kiểm chứng (cần chạy mới kết luận)

Theo quy tắc documentation accuracy — các mục sau là **giả thuyết**, chưa verify:

- Tần suất thực tế của cache va chạm (1.3) — cần log cache key + intent trên traffic thật.
- False-positive ticker (1.4) — cần chạy `_extract_query_tickers` trên tập query lịch sử
  và đếm. Chưa chạy (script phải do user chạy).
- Latency thu được từ 1.5 — cần đo `run_subqueries_node` hiện tại.
- Tỷ lệ turn đi nhánh `decompose` vs `simple` — cần đếm từ `gate` event
  `route_after_clarify`.
