# Bài 22 · LangGraph Agent — Sequential Graph

## Vấn đề cần giải quyết

### Query phân tích thị trường chung
User có thể hỏi:
- "Phân tích thị trường hôm nay"
- "Nhận định thị trường hôm nay và tuần tới"
- "VNINDEX hôm nay thế nào?"

Đây là **market-level query** (không phải ticker cụ thể).  
Agent cần phân biệt: `ticker query` vs `market query` và xử lý khác nhau.

### Flow cho market query
```
user: "phân tích thị trường hôm nay"
  collect:
    get_ohlcv("VNINDEX", days=30)   → ^VNINDEX via yfinance
    get_ohlcv("VN30", days=30)      → ^VN30 via yfinance
    search_news("VNINDEX", days=1)  → general market news (no ticker filter)
  analyze_technical:
    calculate_indicators(vnindex_df)
    calculate_indicators(vn30_df)
  assess_risk:
    analyze_market_sentiment("VNINDEX", days=3)
  synthesize:
    LLM: tổng hợp → báo cáo có trích nguồn
```

### Flow cho stock query
```
user: "phân tích HPG"
  collect:
    get_ohlcv("HPG", days=60)
    search_news("HPG", days=7)      → ticker-filtered news
  analyze_technical:
    calculate_indicators(hpg_df)
  assess_risk:
    analyze_market_sentiment("HPG", days=7)
  synthesize:
    LLM: tổng hợp
```

## Data source cho market indices (đã fix bài 21)

VCI REST API không hỗ trợ index — chỉ có stock ticker.  
Giải pháp: `_VN_INDEX_ALIASES` trong `tools/providers.py` map sang yfinance:

| Input | yfinance symbol |
|-------|----------------|
| VNINDEX, VN-INDEX, HOSE | ^VNINDEX |
| VN30 | ^VN30 |
| HNX | ^HNX |
| HNX30 | ^HNX30 |
| UPCOM | ^UPCOM |

`resolve_ticker("VNINDEX")` → `"^VNINDEX"`, `_detect_provider("VNINDEX")` → `YFinanceProvider`.  
`get_realtime_price` và `get_historical_ohlcv` đều gọi `resolve_ticker` trước khi fetch.

## Triển khai

### Files tạo
- `agents/__init__.py`
- `agents/state.py` — `AgentState` TypedDict (total=False) + `detect_query_type()` + `make_initial_state()`
- `agents/graph.py` — 4 node + `build_graph()` + `save_graph_image()`
- `agents/run.py` — CLI entry point

### Thiết kế quyết định
- State dùng `total=False` (LangGraph partial updates)
- OHLCV lưu ra `outputs/agent_cache/<ticker>_ohlcv.csv` — state chỉ giữ path
- `collect` node: parallel fetch OHLCV + news bằng `ThreadPoolExecutor(max_workers=2)`
- `assess_risk` node: thuần if/else. `std(returns[-14:]) > 0.04` → `HIGH_VOLATILITY`
- Graph image: `draw_mermaid_png()` → fallback Mermaid text nếu playwright thiếu
- Token tracking: `history` list trong state, mỗi synthesize call ghi `{input_tokens, output_tokens, elapsed_seconds}`

## Checklist Bài 22

- [x] `agents/state.py` — TypedDict tối giản, xác nhận import không lỗi
- [x] `agents/graph.py` — LangGraph StateGraph 4 node, compile graph
- [x] Node `collect`: parallel tool calls (OHLCV + news) via ThreadPoolExecutor
- [x] Node `analyze_technical`: gọi `calculate_indicators`
- [x] Node `assess_risk`: if/else thuần, `analyze_market_sentiment` chỉ gọi khi OK
- [x] Node `synthesize`: LLM tổng hợp → Markdown report có trích nguồn
- [x] Detect market query vs stock query → khác nhau ở `collect` node (days, news filter)
- [x] Xuất ảnh sơ đồ graph → `agents/graph.png` (playwright OK)
- [x] Test: `python -m agents.run FPT` → báo cáo có trích nguồn (bị truncate do max_tokens cũ, đã fix)
- [x] Test: `python -m agents.run VNINDEX` → dùng VN30 proxy, báo cáo thị trường
- [x] Chạy 5 mã không crash; số benchmark ghi dưới nhãn **"mốc tuần tự"**

## Benchmark — mốc tuần tự

> Đo ngày 2026-08-26. DeepSeek provider (deepseek-chat). max_tokens=2000.

| Ticker  | Wall time (s) | Input tokens | Output tokens | Total tokens | Risk verdict        | Ghi chú |
|---------|--------------|-------------|--------------|-------------|---------------------|---------|
| FPT     | 139.24       | 738         | 800†         | 1538†        | OK (std=1.59%)      | cold run, auto-fetch; max_tokens=800 (bị truncate) |
| HPG     | 27.45        | 775         | 1500†        | 2275†        | OK (std=1.54%)      | warm run; max_tokens=1500 (bị truncate) |
| VNM     | 132.36       | 749         | 970          | 1719         | OK (std=0.80%)      | cold run, auto-fetch |
| MWG     | 148.16       | 738         | 1027         | 1765         | OK (std=1.60%)      | cold run, auto-fetch |
| VNINDEX | 33.74        | 760         | 2000†        | 2760†        | OK (std=1.08%)      | warm run (no news fetch for index); max_tokens=2000 (bị truncate) |
| **p50 cold** | 139s | — | — | 1719 | — | median cold-run |
| **p50 warm** | 31s  | — | — | 2518 | — | median warm-run (HPG+VNINDEX) |
| **p95**  | ~148s        | —           | —            | ~2760        | —                   | dominated by cold-run auto-fetch |
| **TB tokens** | —   | 752         | 1259         | 2011         | —                   | trung bình 5 mã |

† Bị truncate tại giới hạn — report chưa đầy đủ phần Khuyến nghị.

### Quan sát

- **Cold vs warm:** cold run (auto-fetch cafef+tavily) mất ~130-150s. Warm run ~27-34s. Bài 23+ sẽ đo trên warm run.
- **VNINDEX — dữ liệu không đủ:** collect dùng 30 ngày → MACD cần 26 phiên thực tế (sau weekend) không đủ. MA(50) cần 50 phiên — thiếu hoàn toàn. Fix: tăng `ohlcv_days` market lên 60.
- **max_tokens:** DeepSeek verbose — 2000 vẫn chưa đủ cho báo cáo 5 phần. Cần 2500+ hoặc rút gọn prompt.
- **Không có HIGH_VOLATILITY** trong 5 mã (std 0.8–1.6%, ngưỡng 4%) — thị trường đang bình thường. Cần test case nhân tạo để kiểm tra nhánh này.

### Action items cho bài tiếp theo

- Đổi `ohlcv_days` market từ 30 → 60 trong `agents/graph.py`
- Đổi `max_tokens` từ 2000 → 2500 hoặc thêm instruction "báo cáo tối đa 400 từ"

## Câu hỏi mở

- Forward-looking ("tuần tới thế nào") cần macro data ngoài OHLCV — LLM sẽ phải caveat
- yfinance `^VNINDEX` có delay ~15 phút, không realtime — cần note trong response
- Nếu OHLCV miss (yfinance không có `^VN30`), agent cần fallback gracefully
