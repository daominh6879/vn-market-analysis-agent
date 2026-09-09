# Eval rag_qa — RAGAS

> **2 loại eval khác mục đích, đừng gộp:**
> - **File này** (`evals/eval_rag_qa_ragas.py`) — đo **chất lượng nội dung câu trả lời**, chỉ scope intent `rag_qa`. RAGAS 4 metric cần `(question, answer, contexts, ground_truth)` dạng văn bản — chỉ có ý nghĩa với intent trả lời bằng RAG/SQL free-text. Không áp dụng được cho `price_action`/`technical_analysis`/... vì các intent đó trả số liệu/report có cấu trúc khác, không phải "RAG answer từ context truy hồi".
> - **`evals/eval_router_tools.py`** (xem [eval-router-tools.md](eval-router-tools.md)) — đo **độ chính xác định tuyến + tool**, phủ **tất cả 11 intent**. Không đo chất lượng câu trả lời, chỉ đo: router có chọn đúng intent/ticker không, tool tương ứng có chạy ra data không.
>
> Router gate trong file này (`classify_hybrid()` check) chỉ dùng để loại case lỡ gắn nhãn sai `rag_qa` (ví dụ câu hỏi thực chất route sang `valuation`) trước khi tính RAGAS — không phải bộ đo router đầy đủ.

## RAGAS là gì

Framework đo chất lượng câu trả lời RAG, không cần con người chấm tay. 4 tiêu chí dùng trong `evals/eval_rag_qa_ragas.py`:

| Tiêu chí | Đo gì | Cần `ground_truth`? |
|---|---|---|
| `faithfulness` | Câu trả lời có bám sát ngữ cảnh truy hồi (`contexts`), không bịa thêm claim | Không |
| `answer_relevancy` | Câu trả lời có đúng trọng tâm câu hỏi (so sánh embedding câu hỏi sinh ngược từ answer với câu hỏi gốc) | Không |
| `context_precision` | Ngữ cảnh truy hồi có ít nhiễu — so với `ground_truth` | Có |
| `context_recall` | Ngữ cảnh truy hồi có đủ thông tin để trả lời đúng — so với `ground_truth` | Có |

Cơ chế chấm: một LLM "judge" đọc `(question, answer, contexts, ground_truth)`, tách answer thành từng claim, kiểm tra claim có được context support không (faithfulness), hoặc so khớp câu hỏi/ngữ cảnh với ground_truth (2 tiêu chí còn lại). Không dùng OpenAI mặc định — script trỏ judge về DeepSeek (`llm.factory` cùng provider hệ thống dùng) + embeddings về Ollama `bge-m3` (đúng `EMBED_MODEL` project dùng), tránh lệ thuộc OpenAI credit.

## Bộ câu hỏi (`RAG_QA_CASES`)

Lấy từ nhóm intent `rag_qa` trong `tests/test_router_intents_cache.py` (`CLASSIFY_CASES`/`GATHER_CASES`):

| Câu hỏi | Ticker |
|---|---|
| Doanh thu HPG năm 2024 là bao nhiêu? | HPG |
| Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu? | HPG |

`ground_truth` **không hard-code** — script tự query `rag.sql_agent.execute_safe()` lấy số thật từ Postgres tại thời điểm chạy, in ra màn hình để soát tay trước khi tin điểm số.

### Mục đích từng câu — expect gì

| # | Câu hỏi | Ticker | Mục đích (test cái gì) | Expect |
|---|---|---|---|---|
| 1 | Doanh thu HPG năm 2024 là bao nhiêu? | HPG | Câu đơn giản nhất — 1 metric, 1 số. Baseline: pipeline chạy đúng ở case dễ nhất trước khi xét case khó. | Router → `rag_qa`. Context = đúng dòng `doanh_thu_thuan`/tương đương. Answer đúng số, đơn vị đổi tỷ đồng. faithfulness/relevancy/precision/recall cao (retrieval đơn giản, ít khả năng lẫn). |
| 2 | Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu? | HPG | **2 metric trong 1 câu** — test SQL agent có gộp đúng 2 giá trị (doanh thu + lợi nhuận) hay bị lẫn/thiếu 1 cái. | Answer phải có đủ CẢ 2 số, không thiếu vế nào. Nếu SQL chỉ trả 1 metric → answer thiếu, faithfulness/recall giảm — bắt được lỗi gộp metric. |
| 3 | EPS của HPG năm 2024 là bao nhiêu? | HPG | **Metric dạng tỷ số** (EPS = lợi nhuận / số cổ phiếu, không phải cột thô có sẵn) — test SQL agent có tính đúng hay lấy nhầm cột gần giống tên. | Answer đúng phải ≈ 1.505 VND/cổ phiếu (`lai_co_ban_tren_co_phieu_vnd`). **Đã phát hiện bug thật** (xem mục Bug #4 dưới): SQL agent trả nhầm lợi nhuận tuyệt đối (12.020 tỷ) thay vì EPS. |
| 4 | Tổng tài sản HPG năm 2024 là bao nhiêu? | HPG | Metric bảng cân đối kế toán (balance sheet) khác nhóm với doanh thu/lợi nhuận (income statement) — test SQL agent định vị đúng bảng/cột khi đổi nhóm báo cáo. | Answer ≈ 224.489,7 tỷ đồng (`tong_tai_san`). |
| 5 | Nợ vay dài hạn của HPG năm 2024 là bao nhiêu? | HPG | Câu hỏi dùng từ khóa tiếng Việt khác cách viết cột DB (`vay_dai_han` — "nợ vay" vs "vay") — test SQL agent map đúng từ khóa tự nhiên → tên cột kỹ thuật. | Answer ≈ 27.080,4 tỷ đồng. |
| 6 | Biên lợi nhuận gộp của HPG năm 2024 là bao nhiêu? | HPG | Metric tỷ số thứ 2 (gross margin % = lợi nhuận gộp / doanh thu × 100) — cùng nhóm rủi ro như EPS (#3), test lại xem SQL agent có lặp lại lỗi tính tỷ số hay không. | Answer đúng phải ≈ 13.32%. **Đã phát hiện bug thật** — SQL agent trả nhầm lợi nhuận gộp tuyệt đối (18.497,55 tỷ) thay vì tỷ lệ %. |
| 7 | Lưu chuyển tiền thuần từ hoạt động kinh doanh của HPG năm 2024? | HPG | Metric ở báo cáo lưu chuyển tiền tệ (cash flow statement) — nhóm báo cáo thứ 3, câu hỏi dài/nhiều từ — test SQL agent + router không bị nhiễu bởi câu dài. | Answer ≈ 6.608,3 tỷ đồng (`luu_chuyen_tien_te_rong_tu_cac_hoat_ong_san_xuat_kinh_doanh`). |

**Logic chọn bộ câu hỏi:** phủ nhiều nhóm báo cáo khác nhau trong schema `financial_facts` — income statement (#1, #2, #6), balance sheet (#4, #5), cash flow (#7) — không chỉ lặp lại 1 metric. #3 (EPS) và #6 (biên lợi nhuận gộp) cố tình đặt cùng nhóm "metric tính từ tỷ số" (chia 2 cột, không lấy thẳng 1 cột) — đây là nhóm rủi ro cao nhất, đã lộ ra 2/2 bug thật khi chạy thực tế (xem mục Bug bên dưới).

## Cách chạy

```bash
.venv\Scripts\activate
python evals/eval_rag_qa_ragas.py
python evals/eval_rag_qa_ragas.py --out evals/rag_qa_ragas_eval.json   # tùy chọn đổi output path
```

Yêu cầu: Postgres + Qdrant chạy, `.env` có `DEEPSEEK_API_KEY`, `OLLAMA_BASE_URL`/`OLLAMA_EMBED_MODEL`. Không mock — `retrieve_only()` lấy context thật, `answer()` sinh trả lời thật qua DeepSeek.

Router/classify (`llm_route`, `classify_hybrid`) đi qua `llm.factory.create_client()` → tự trace Langfuse (khi có `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`) — không cần wiring thêm.

## Kết quả lần chạy 2026-09-09

| Câu hỏi | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| Doanh thu HPG 2024 | 0.50 | 0.996 | 1.0 | 1.0 |
| Doanh thu + lợi nhuận HPG 2024 | 0.00 | 0.977 | 1.0 | 1.0 |

context_precision/recall = 1.0 cả 2 case → retrieval đúng, đủ info, không nhiễu.
faithfulness thấp bất thường trong khi câu trả lời đúng số liệu — nghi judge chấm khắt khe việc reformat số (nghìn tỷ↔tỷ, số chữ số thập phân) thành "claim không được context support trực tiếp", không phải lỗi retrieval/generation thật. Chưa verify thêm.

## Router gate (thêm 2026-09-09)

Mỗi câu chạy qua `classify_hybrid()` thật trước khi vào pipeline — check `intent == "rag_qa"` và `ticker == expected`. Case route sai bị **SKIP khỏi RAGAS**, không âm thầm chấm điểm case đi sai pipeline. Ví dụ thật bắt được: `"ROE của HPG năm 2024 là bao nhiêu?"` bị router route thành `valuation`, không phải `rag_qa` → tự động loại. Kết quả gate (`routing_report`) lưu vào output json.

Bộ câu hỏi mở rộng (8 câu, đều ticker HPG — data scope chỉ có HPG): doanh thu, doanh thu+lợi nhuận, EPS, ROE, tổng tài sản, nợ vay dài hạn, biên lợi nhuận gộp, lưu chuyển tiền thuần.

## Bug đã gặp + đã sửa

1. `ragas==0.4.3` (mọi bản 0.4.x) crash import: `langchain_community.chat_models.vertexai` bị gỡ khỏi `langchain-community==0.4.2` đang cài. Fix: thêm shim `.venv/Lib/site-packages/langchain_community/chat_models/vertexai.py` (stub `ChatVertexAI`, không dùng thật — `LLM_PROVIDER=deepseek`).
2. `ragas.evaluate()` mặc định gọi OpenAI làm judge → lỗi 429 `credit_balance_exhausted` (project không cấu hình OpenAI key có credit). Fix: truyền `llm=` (DeepSeek qua `ChatOpenAI` + base_url DeepSeek) và `embeddings=` (Ollama `bge-m3`) tường minh vào `evaluate()`.
3. Judge bị cắt giữa câu (`LLMDidNotFinishException`) → tăng `max_tokens=2048` cho judge LLM.
4. **Context ≠ answer (bug thật trong `rag/qa.py`, phát hiện khi mở rộng bộ câu hỏi):** `retrieve_only()` và `answer()`/`_sql_answer()` mỗi bên tự gọi `execute_safe()` riêng — text-to-SQL không deterministic, nên 2 lần gọi độc lập cho cùng câu hỏi có thể ra SQL/rows khác nhau. Biểu hiện: case "Biên lợi nhuận gộp" context có data đúng nhưng `answer()` trả lời "Không có dữ liệu"; case "EPS" context hiện metric khác với số answer() dùng. Fix: tách `_fetch_sql_rows_text()` làm nguồn sự thật duy nhất, `answer()` không tự query lại — dùng đúng rows đã fetch để sinh câu trả lời (`rag/qa.py`). Script eval cũng sửa để không gọi `retrieve_only()`+`answer()` tách rời cho route `số_liệu` (dùng chung 1 lần fetch).

## Phát hiện chưa sửa — SQL agent tính sai metric dạng tỷ số

Sau khi sửa bug context≠answer, lộ ra vấn đề sâu hơn: `execute_safe()` (text-to-SQL) tính **sai giá trị** cho các chỉ số derived (chia 2 cột):
- "EPS của HPG năm 2024" → SQL trả `eps_billion_vnd = 12020.02` — đó là **lợi nhuận sau thuế**, không phải EPS/cổ phiếu (EPS đúng là `lai_co_ban_tren_co_phieu_vnd = 1505` VND, thấy ở ground_truth run trước).
- "Biên lợi nhuận gộp của HPG năm 2024" → SQL trả `gross_margin_billion_vnd = 18497.55` — đó là **lợi nhuận gộp tuyệt đối** (`loi_nhuan_gop`), không phải tỷ lệ % (đúng là `gross_profit_margin_pct = 13.32`, thấy ở lần chạy khác của cùng câu hỏi).

Chưa sửa — cần cải prompt/schema hint cho `rag/sql_agent.py` để LLM hiểu "EPS"/"biên lợi nhuận" là tỷ số cần chia, không phải lấy thẳng 1 cột. Ngoài scope hiện tại (thêm câu hỏi + router gate).

## Rủi ro chưa xử lý

`ground_truth` lấy qua `execute_safe()` — SQL agent free-text, không deterministic, đôi khi sinh SQL hỏng (`SecurityError` từ sqlglot parse fail) — script đã bọc try/except, case đó chỉ mất context_precision/recall thay vì crash cả run. Nếu dùng script này cho báo cáo chính thức: nên verify tay `ground_truth` một lần rồi pin thành hằng số trong `RAG_QA_CASES`, không để `execute_safe()` tự sinh lại mỗi lần chạy.
