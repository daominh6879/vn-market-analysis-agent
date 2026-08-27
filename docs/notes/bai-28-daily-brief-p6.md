# Bài 28 — Daily Brief Phase 6: Report Agent + Template + Schedule

## Kết quả

- **33 tests xanh** (`tests/test_phase6.py`)
- **LangGraph mới**: `agents/market_brief_graph.py` — 3 node: `collect_all → compose_outlook → render_report`
- **Template**: `agents/templates/market_brief.txt` — khung cố định, 12 placeholder
- **CLI**: `agents/run_brief.py --date YYYY-MM-DD --out info/DD_MM_YYYY.txt`

## Artifacts tạo ra

| File | Vai trò |
|---|---|
| `agents/market_brief_state.py` | `MarketBriefState` TypedDict — chỉ giữ string/dict nhỏ, không giữ DataFrame |
| `agents/market_brief_graph.py` | LangGraph: collect_all (4 threads) → compose_outlook (LLM) → render_report (template) |
| `agents/templates/market_brief.txt` | Template Python .format() — copy đúng format `info/26_08_2026.txt` |
| `agents/run_brief.py` | CLI runner: `--date`, `--out`, `--no-file` |
| `tests/test_phase6.py` | 33 tests: state, template, mỗi sub-collector, compose_outlook, full graph e2e |

## Thiết kế chính

### Luồng dữ liệu

```
start
  └─> collect_all  (ThreadPoolExecutor 4 workers song song)
        ├── _collect_world()    → world_block, gold_oil_block, crypto_block, fx_block
        ├── _collect_vn()       → vn_index_text, breadth_text, movers_text, foreign_text, sector_text
        ├── _collect_news()     → news_text, events_text, broker_text
        └── _collect_technical()→ tech_signals, candle_pattern, levels_text
  └─> compose_outlook (1 LLM call — chỉ viết phần 🎯 NHẬN ĐỊNH)
  └─> render_report   (Python template.format() — LLM không viết số)
  └─> END
```

### Guardrail số liệu

- **Rule cứng**: LLM chỉ viết văn phần 🎯 NHẬN ĐỊNH. Tất cả số liệu (VN-Index, breadth, crypto...) render từ data thực.
- **Thiếu data** → in `(không có dữ liệu)`, không để LLM tự điền.
- `missing_fields: list[str]` log mọi field thiếu trong state.

### Import pattern

- Tất cả tool functions import ở module-level trong `market_brief_graph.py` → pytest có thể patch tại `agents.market_brief_graph.<name>`.
- `create_client` / `LLMMessage` lazy-import với fallback nếu `llm.factory` không sẵn lúc test.

### DB-first cho VN data

- VN-Index: `tools.index_db.query_index_latest("VNINDEX")` → fallback `get_market_performance("today")`.
- Breadth / movers / foreign / sector: qua các tools đã có từ Phase 1-2 (DB-first với live fallback).

## DoD check

DoD Phase 6: "`python agents/run_brief.py --date 2026-08-26` ra file gần khớp `info/26_08_2026.txt`; mọi số truy được về Postgres."

- Graph biên dịch và import thành công.
- 33 tests xanh: template có đủ 12 placeholder + 6 section header, render đúng date format DD/MM/YYYY, guardrail `(không có dữ liệu)`, LLM failure fallback, write file, full graph e2e.
- Khi chạy thật: cần DB có data và LLM_PROVIDER=deepseek trong `.env`.

## Lệnh chạy

```bash
# Sinh bản tin hôm nay:
python agents/run_brief.py

# Sinh bản tin theo ngày cụ thể:
python agents/run_brief.py --date 2026-08-26

# Sinh và ghi ra file tùy chọn:
python agents/run_brief.py --date 2026-08-26 --out info/26_08_2026.txt

# Chỉ in ra stdout, không ghi file:
python agents/run_brief.py --date 2026-08-26 --no-file

# Chạy tests:
python -m pytest tests/test_phase6.py -v
```

## Quyết định kỹ thuật

- **ThreadPoolExecutor 4 workers** trong `collect_all` node thay vì LangGraph native parallel edges — đơn giản hơn, cùng hiệu quả, ít boilerplate.
- **Module-level imports** (không phải lazy trong từng hàm) để pytest `patch` hoạt động đúng tại `agents.market_brief_graph.<name>`.
- **Template `.format()`** thay vì Jinja2 — không thêm dependency, đủ dùng cho template tĩnh.
- **Không thêm Dagster schedule** trong file này — để làm ở phase tiếp theo khi cần CI automation.
