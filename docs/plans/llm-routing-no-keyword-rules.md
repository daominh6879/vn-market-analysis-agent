# Plan: bỏ keyword/regex routing, để router LLM quyết định

Mục tiêu: xóa `agents/intent_rules.py` và các bộ nhận diện regex/keyword trong `agents/guardrails.py`.
Mọi quyết định (intent, topic dữ liệu, ngoài phạm vi, ngôn ngữ, cờ rủi ro) do router/classifier LLM trả về qua tool schema.

## Giữ lại (không phải bộ nhận diện)
- Văn bản cố định: `OFF_TOPIC_REPLY(_EN)`, `_DISCLAIMERS_*`, `prompt_rules_for`, `_MARKET_RULES_SHEET`. Disclaimer vẫn do Python nối vào, không để LLM tự viết.
- `scrub_internal` + `_INTERNAL_*_RE`: lọc tên bảng nội bộ khỏi dữ liệu gửi người dùng (lọc đầu ra, không phải nhận diện ý định).
- Ngoài phạm vi của plan này: `is_screening_query` / `_parse_filters` (classifier, screening).

## Chuyển từng quy tắc sang LLM

| Cũ (regex) | Mới (LLM) |
|---|---|
| `_TOPIC_PATTERNS` (movers/index/foreign/liquidity/marketcap/list) | `market_topics` đã có; bỏ phần hợp regex, rỗng thì mặc định `movers`+`index` |
| `CLOSING_RE` | intent `closing_commentary` trong mô tả schema |
| `_VOLUME_SPIKE_RE` | schema `screening`: đột biến khối lượng → filter `volume > 1.5`; bỏ fallback ở `screening.gather_data` |
| `_TECH_RE`, `_HISTORY_RE` | quy tắc trong mô tả intent `technical_analysis` / `price_action` |
| `_DIVIDEND_RE`, `_QUARTER_RE`+`_FIN_ITEM_RE`, `_YEARLY_METRIC_RE`, marketcap của 1 mã | trường mới `rag_topics` (`dividend`, `quarterly`, `yearly_metric`, `marketcap`); `rag_extras.gather_extras` đọc topics |
| `_COMMODITY_RE`, `_CRYPTO_RE` | mô tả `needs_agent_run` (vàng/dầu → `macro_sector`) và `out_of_scope` (crypto) |
| `MARKET_RULES_RE` | tool mới `market_rules_answer` trong router |
| `is_off_topic`, `is_social_turn`, `has_finance_terms` | `out_of_scope` / `direct_reply` tool (đã có); bỏ gate trước LLM và các kiểm tra sau |
| `detect_language` | trường `language` (`vi`/`en`) |
| `risk_flags` | trường `risk_flags` (`advice`/`personal`/`prediction`) |

Thread dữ liệu: tool schema (router + classifier) → `RouterResult`/`RouteResult` → `turn_handler._build_agent_state` → `AgentState` → `graph` (single subtask / decompose / run_subqueries / synthesize_final).

## Các bước
1. Baseline: chạy toàn bộ `tests/` để biết lỗi có sẵn.
2. Viết test hợp đồng offline (fake LLM) trước: `tests/test_llm_routing_contract.py`.
3. Sửa schema + dataclass + state + turn_handler.
4. Sửa router (`llm_route`, `_parse_agent_fields`, `_fallback_classify`), `classify_node`, gather (`market_data`, `rag_extras`, `screening`), `synthesize_final`.
5. Xóa `intent_rules.py`, bỏ detector khỏi `guardrails.py`, xóa/đổi test regex cũ.
6. Chạy lại toàn bộ test; không được có lỗi mới so với baseline.
7. Eval thật với LLM: `evals/eval_routing_parity.py` (mọi câu hỏi mà quy tắc cũ từng bảo vệ). Mục tiêu: không lùi so với test cũ.
8. Cập nhật NOTES.md/EXPLAIN.md/QA.md sau khi có kết quả thật.

## Tiêu chí xong
- Không file nào import `agents.intent_rules`; module không còn tồn tại.
- `guardrails.py` không còn hàm nhận diện ý định bằng regex/keyword.
- Test offline xanh; eval routing thật đạt ngưỡng đã ghi trong script.

## Trạng thái (2026-10-12)
Bước 1–8 đã xong. Giai đoạn 2 (screening regex, `focus.py` continuation/alias) cũng đã gỡ; xem `docs/notes/llm-routing-no-keyword-rules.md`. Còn lại ngoài phạm vi: danh sách mã/nhãn ngành từ DB vẫn dùng để đối chiếu thực thể.
