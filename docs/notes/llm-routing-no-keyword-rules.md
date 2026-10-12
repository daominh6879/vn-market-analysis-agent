# Router LLM thay keyword rules

Kế hoạch: `docs/plans/llm-routing-no-keyword-rules.md`. Khái niệm: xem EXPLAIN.md ("Vì sao để router LLM quyết định...").

## Thay đổi
- Xóa `agents/intent_rules.py`; xóa các hàm nhận diện regex/keyword trong `agents/guardrails.py` (giữ văn bản cố định, `disclaimer_for`, `prompt_rules_for`, `scrub_internal`).
- Router (`agents/conversation_router.py`) và classifier (`agents/classifier.py`) trả thêm `rag_topics`, `risk_flags`, `language`; luật định tuyến chuyển vào mô tả schema (`ROUTING_RULES` dùng chung).
- Tool mới `market_rules_answer` cho câu hỏi luật giao dịch (thay `MARKET_RULES_RE`).
- Intent mới `closing_commentary` (`agents/intents/closing_commentary.py`).
- Gather chỉ dùng topic do router chọn: `market_data` (rỗng -> movers + index), `rag_extras.gather_extras(..., topics)`, `screening` không còn tự chèn filter volume.
- `synthesize_final` đọc `risk_flags`/`language` từ state.

## Kiểm chứng (số liệu thật)
- Test offline hợp đồng: `tests/test_llm_routing_contract.py` — 34 passed.
- Eval LLM thật: `evals/eval_routing_parity.py` — 29/29 = 100% (lần chạy đầu 28/29: "Giá bitcoin và vàng" trả `mixed` — từ chối bitcoin, trả lời vàng; chấp nhận vì tốt hơn hành vi cũ là từ chối cả câu).
- Chạy từng file test (không e2e): mọi file xanh trừ các lỗi có sẵn ở HEAD (xác nhận bằng worktree sạch): `test_regression_fixes::test_market_brief_beats_investment_case`, `test_router_intents_cache::test_valuation_gather_explicit_tickers_compare`, `::test_ngan_hang_thinh_vuong_resolves_vpb`; `test_episodic_memory`, `test_minio_pipeline`, `test_pipeline` treo (cần dịch vụ ngoài), `test_token_streaming` chậm (~90s).

## Chạy lại
```
pytest tests/test_llm_routing_contract.py
python evals/eval_routing_parity.py --out evals/routing_parity_eval.json
```

## Giai đoạn 2: gỡ nốt screening / focus
- Xóa `is_screening_query`, `_looks_like_screening`, `_screening_route`, `_parse_filters`, `_parse_rsi_filter` (regex lọc). Screening chỉ nhận `screening` có cấu trúc từ router; không có filter thì dùng top-N SQL cũ.
- `agents/focus.py`: xóa `_CONTINUATION_MARKERS` và `_SECTOR_ALIASES`. `resolve_focus` chỉ trả `FRESH` (có mã trong câu) hoặc `AMBIGUOUS`; việc kế thừa intent/chủ thể của lượt trước do LLM quyết định qua `_inject_last_context` (một đoạn ghi chú duy nhất, không còn `inherit_intent`). Giữ: đối chiếu mã với danh sách mã thật và nhãn ngành từ DB (tra thực thể, không đoán ý định).
- Test cập nhật: `test_focus`, `test_screening_filters`, `test_screening_rsi`, `test_router_intents_cache` (bỏ test guard screening/regex/continue-fallback, đổi test follow-up sang kiểm tra chỉ dẫn gửi cho LLM). Thêm `tests/test_closing_commentary.py` (3 test).
- Kết quả: `test_router_intents_cache` + `test_llm_routing_contract` + `test_focus` = 149 passed, 2 failed (cả 2 là lỗi có sẵn trên HEAD: `test_valuation_gather_explicit_tickers_compare`, `test_ngan_hang_thinh_vuong_resolves_vpb`). Routing parity chạy lại: 29/29.

## Eval end-to-end từng intent (LLM + tool thật)
`python evals/eval_intents_e2e.py` (đường production: `llm_route` -> `_build_agent_state` -> graph.invoke), 19 case phủ 12 intent, kết quả `evals/intents_e2e_eval.json`: **19/19** theo kiểm tra tự động (đúng intent, không hỏi lại, đủ độ dài, có từ khóa kỳ vọng, không rò tên nội bộ). Ghi chú sau khi đọc nội dung:
- `closing_commentary`: đủ 4 mục, số liệu khớp dữ liệu nguồn (VN-Index 1,735.09; thanh khoản 165% TB 19 phiên; khối ngoại ròng -9,714 tỷ). Lần đầu mục 4 chỉ liệt kê mốc, không có kịch bản; sau khi sửa `CLOSING_RULE` (yêu cầu 2 kịch bản có điều kiện theo mốc) mục 4 có kịch bản "giữ/thủng đáy 1,724.17; vượt MA20 1,779.01".
- `screening_rsi`: đã kiểm chứng từ `ohlcv_daily`. KOS RSI 1.35 là đúng (giảm sàn liên tục, close 30.05 → 10.35 trong 16 phiên). BCG RSI 0.00 là lỗi dữ liệu: `volume = 0` ít nhất 30 phiên, giá đứng yên nên RSI vô nghĩa nhưng vẫn lọt "RSI < 30". Đã sửa: `_is_untraded` bỏ mã có tổng volume 5 phiên cuối = 0 khỏi mọi screening kỹ thuật (test `test_untraded_ticker_is_skipped_by_technical_screen`). Chạy lại: 68 → 66 mã (BCG và DTT biến mất), KOS đứng đầu.
- Log khi chạy: DB giá tới 2026-10-09 (hôm chạy là 2026-10-12, chưa có phiên mới); `fx_scraper` SBV trả 403 và mirror tỷ giá cũ (2025-04-24) bị bỏ qua.

## Xử lý các vấn đề còn lại (2026-10-12)
- 3 test lỗi có sẵn đã sửa, mỗi cái có nguyên nhân kiểm chứng:
  - `test_valuation_gather_explicit_tickers_compare`: mock `_build_analysis` trong test chỉ nhận 2 tham số, code thật gọi 3 (`'TB nhóm so sánh'`) -> sửa mock.
  - `test_ngan_hang_thinh_vuong_resolves_vpb`: alias "ngân hàng thịnh vượng" có trong `TICKER_ENRICHMENT` nhưng `load_hose_universe()` đọc `hose_tickers.json` và không gộp alias đã biên soạn, nên DB `securities` thiếu alias. Sửa: `_union` gộp alias/tags từ `TICKER_ENRICHMENT` vào bản ghi cache; chạy lại `seed_from_json()` (upsert 405 mã).
  - `test_market_brief_beats_investment_case`: LLM trả `investment_case` cho "VNINDEX có nên mua không?". Thêm luật vào `ROUTING_RULES`: câu hỏi về chỉ số thị trường là `market_brief`, `investment_case` chỉ cho một mã. Chạy 3 lần liên tiếp đều pass.
- DTT cũng có volume 0 (5+ phiên, giá đứng 15.8) nên bị loại khỏi screening kỹ thuật là đúng.
- `closing_commentary` với `STRICT_NEUTRAL=true`: mục 4 chỉ nêu kịch bản theo mốc, không có từ khuyến nghị mua/bán (các từ "mua/bán" xuất hiện chỉ là số liệu khối ngoại). Khi mọi chỉ số thiếu dữ liệu, `gather_data` trả "Không có dữ liệu chỉ số phiên gần nhất." (đã có test).
- Eval nhiều lượt `python evals/eval_multiturn_routing.py`: 6/6 (so sánh 2 mã -> "phân tích sâu hơn" giữ cả BID và CTG; "giá cổ phiếu?" sau P/E phân loại intent mới; mã mới không kế thừa mã cũ).
- Kết quả cuối: `test_router_intents_cache` 110 passed; `test_llm_routing_contract` 34; `test_focus` 7; `test_screening_*` 9; `test_closing_commentary` 3; `test_report_fixes` 5; `test_data_gaps` 33; `test_conversation_memory` 10 (~92s); `test_token_streaming` 4 (~118s). Routing parity 29/29, per-intent e2e 19/19, multi-turn 6/6.
- Dữ liệu giá đã có phiên 12/10 khi chạy lại `closing_commentary`.

## Sửa theo báo cáo thử trên UI (2026-10-12)
Chạy lại 8 câu của báo cáo qua `llm_route` thật trước khi sửa: "Tổng kết phiên hôm nay" đã đúng `closing_commentary` (nhãn "Thị trường" trong báo cáo không phải do router); "ROE của HPG" -> `rag_qa`; "Phân tích HPG" -> `investment_case`; Bitcoin và bài thơ -> router đã chọn tool `out_of_scope` (type `text`) nhưng route không mang `intent` nên UI hiện "Hội thoại".
- Prompt mâu thuẫn: `agents/classifier.py` vừa nói "analyze a ticker -> technical_analysis" vừa có investment_case "comprehensive analysis"; và "không có ý định tài chính -> conversation" đối nghịch với "việc phi tài chính -> out_of_scope". Sửa: "phân tích HPG" (không chỉ báo/chỉ số) -> `investment_case`, chỉ báo -> `technical_analysis`; `conversation` chỉ cho chào hỏi/cảm ơn, mọi thứ ngoài chứng khoán VN -> `out_of_scope`.
- Luật mới trong `ROUTING_RULES`: chỉ số kèm năm/kỳ cụ thể -> `rag_qa`; cùng chỉ số không nêu kỳ ("ROE của HPG", "P/E của VCB") -> `valuation`.
- `llm_route` gắn `intent="out_of_scope"` cho route từ chối (vẫn `type="text"`, không chạy agent); `turn_handler` phát nhãn này thay cho "conversation".
- UI (`ui/chat.py`, `ui/chainlit_app.py`): thêm nhãn cho `valuation`, `rag_qa`, `investment_case`, `market_data`, `breakout_scan`, `out_of_scope` (trước đó hiện tên intent thô).
- Kết quả thật: probe 12 câu đúng như kỳ vọng; "ROE của HPG" nay -> `valuation`, trả 260 ký tự (một chỉ số + so sánh nhóm), không còn báo cáo 10 mục. Parity 37/37 (thêm 8 case), nhiều lượt 6/6, per-intent e2e 19/19, `test_llm_routing_contract` 36 passed (thêm 2: nhãn out_of_scope, UI đủ nhãn cho mọi intent), `test_router_intents_cache` 110, `test_regression_fixes` 19.
- Chưa sửa: câu trả lời từ chối vẫn là văn bản do LLM soạn (có ngữ cảnh) thay vì `OUT_OF_SCOPE_REPLY` cố định; nếu muốn đồng nhất thì đổi `llm_route` để luôn trả text cố định.
