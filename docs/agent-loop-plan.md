# Agent Loop — Apply Plan

## Agent loop nghĩa gì

Classic pattern: LLM chọn action → run tool → observe result → LLM đánh giá đủ chưa → lặp lại tới khi đủ hoặc hit max iter. Khác DAG cố định (fixed sequence, không ai "quyết định" giữa đường).

## Hiện trạng — đã có loop chỗ nào

`agents/graph.py`:
- `fusion_search → grade_or_critique → decide_next → fusion_search` (rewrite loop, `MAX_ITER=2`) — **có loop thật**, nhưng `grade_or_critique` là if/else thuần (đếm `len(fused_chunks)`), không LLM đánh giá chất lượng.
- Phần còn lại (`decompose_node → run_subqueries_node → synthesize_final`) là **DAG 1 lần**, không lặp. Sub-tasks cố định từ đầu, không re-plan nếu data thiếu.
- `assess_risk`, `pick_branch`, `decide_next`: pure logic, no LLM — đúng chủ ý design (comment ghi rõ "no LLM call").

Kết luận: project có loop dạng retrieval-retry (RAG), chưa có loop dạng agent tự quyết định/tự phê bình output cuối.

## Gaps — áp dụng thêm được đâu

### 1. `grade_or_critique` — nâng cấp lên LLM critique (RAG loop)
Giờ chỉ đếm số chunks. Đổi thành LLM chấm "chunks đủ trả lời câu hỏi chưa" — bắt đúng case đủ SỐ LƯỢNG nhưng SAI NGỮ CẢNH (hay gặp với BCTC nhiều quý).
- File: `agents/graph.py::grade_or_critique`
- Risk: thêm 1 LLM call/iteration → cost + latency tăng. Cap `MAX_ITER=2` giữ nguyên.

### 2. `synthesize_final` — self-critique loop trước khi trả report
Sau khi LLM viết report, thêm 1 pass: LLM tự kiểm "mọi claim có citation không, có match Xong-khi-checklist không" (CLAUDE.md đã định nghĩa checklist). Nếu fail → retry synthesize với feedback, cap 1-2 lần.
- File: `agents/graph.py` — node mới `critique_report_node`, edge `synthesize_final → critique_report_node → (retry synthesize_final | cache_save_node)`
- Đây match đúng rule "Per-lesson completion" trong CLAUDE.md (evaluate quality, fix bugs) — nhưng làm ở agent-level thay vì tay.

### 3. `run_subqueries_node` — re-plan nếu sub_results rỗng/thiếu
Hiện: gather 1 lần theo `sub_tasks` cố định từ `decompose_node`, không check kết quả. Nếu ticker lỗi hoặc data rỗng, không có bước 2.
- Thêm: sau gather, nếu >50% sub_results rỗng → gọi lại `decompose_node` với note "sub-task X failed, thử câu hỏi khác" — cap 1 retry.
- File: `agents/graph.py::run_subqueries_node` + edge mới về `decompose_node`

### 4. Tool-use loop cho intent phức tạp (investment_case, macro_sector)
Hiện các intent này gather data cố định (`gather_data()` fixed set of tools). True agent loop = LLM quyết định tool nào cần gọi tiếp dựa trên data đã có (ReAct). Effort lớn, đổi kiến trúc `agents/intents/*.py`.
- Cân nhắc kỹ — có thể **không cần** nếu `_COMPLEX_INTENTS` fixed gather đã đủ tốt (chưa có bằng chứng thiếu). Để item 4 cuối, chỉ làm nếu 1-3 không đủ.

## Priority

| # | Item | Effort | Giá trị |
|---|------|--------|---------|
| 1 | grade_or_critique → LLM | Nhỏ | RAG trả đúng hơn |
| 2 | critique_report_node | Trung | Report tự sửa lỗi trước khi trả user |
| 3 | re-plan on empty sub_results | Trung | Giảm case "no_data" khi ticker miss |
| 4 | full ReAct tool loop | Lớn | Chỉ làm nếu 1-3 chưa đủ |

## Việc trước khi code

- Đo hiện trạng: bao nhiêu % report thiếu citation / bao nhiêu sub_results rỗng thực tế (grep trace logs `outputs/agent_cache` hoặc Langfuse) — tránh build loop cho vấn đề chưa xác nhận có thật.
- Set cap rõ cho mọi loop mới (LLM call tăng = cost tăng) — theo `MAX_ITER` pattern đã có.
