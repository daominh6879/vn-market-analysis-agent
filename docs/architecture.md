# Kiến trúc hệ thống RAG — ai-engineer

> Cập nhật sau mỗi bài. Ghi rõ bài nào thêm/thay đổi gì ở cuối file.

---

## Tổng quan

RAG pipeline cho tài liệu tài chính tiếng Việt (HPG BCTC). Kết hợp báo cáo tài chính PDF, tin tức thời gian thực, và số liệu có cấu trúc từ Postgres để trả lời câu hỏi phân tích tài chính. Từ bài 22+ phát triển thêm agent graph (single-ticker + daily market brief).

---

## Stack

| Layer | Công nghệ |
|---|---|
| LLM | DeepSeek (default), Anthropic/Ollama/OpenAI/Gemini (qua factory) |
| Vector store | Qdrant |
| Relational DB | Postgres |
| Object storage | MinIO |
| Pipeline orchestration | Dagster |
| RAG graph | LangGraph |
| Agent graph | LangGraph (agents/) |
| MCP server | FastMCP (tools/mcp_server.py) |
| Web search | Tavily |
| Financial data API | vnstock, VCI price board, SSI iBoard |
| Embedding | bge-m3 (default), nomic-embed-text (Ollama) |
| Eval | RAGAS + golden YAML |
| Reranker | CrossEncoder (cross-encoder/ms-marco-MiniLM-L-6-v2) |
| Observability | Langfuse (optional tracing wrapper) |

---

## LLM Factory

`llm/factory.py::create_client()` — đọc `LLM_PROVIDER` từ env → trả `LLMClient`.

| Provider | Impl | Default model |
|---|---|---|
| `deepseek` (default) | `OpenAIClient` với `base_url=api.deepseek.com` | `deepseek-chat` |
| `anthropic` | `AnthropicClient` | `claude-opus-5` |
| `openai` | `OpenAIClient` | `gpt-4o` |
| `ollama` | `OllamaClient` | `llama3` (local) hoặc `gpt-oss:20b` (cloud) |
| `gemini` | `GeminiClient` | `gemini-2.0-flash` |

Langfuse wrapping: tự động nếu `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` set.

**Rule:** Luôn dùng `create_client()`. Không import provider trực tiếp.

---

## Nguồn dữ liệu

| Nguồn | Nội dung | Lưu ở đâu | Tag trong context |
|---|---|---|---|
| PDF BCTC (HPG) | Báo cáo tài chính đã kiểm toán | Qdrant `hpg_b7_structural_meta` | `RAG corpus` |
| CafeF / VnExpress RSS | Tin tức tài chính, thép, TTCK | Postgres `news_articles` + Qdrant `news_chunks` | `TIN TỨC` |
| vnstock Finance API | KQKD, CDKT, LCTT (annual) | Postgres `financial_facts` (source='vnstock') | `GIÁ LỊCH SỬ` |
| vnstock Prices | Giá đóng cửa điều chỉnh hàng ngày | Postgres `stock_prices` | `GIÁ LỊCH SỬ` |
| LLM extract từ PDF | Số liệu tài chính (fallback) | Postgres `financial_facts` (source='pdf') | `GIÁ LỊCH SỬ` |
| Tavily web search | Kết quả tìm kiếm thời gian thực | Không lưu (real-time) | `WEB` |
| VCI price board | OHLCV hàng ngày (HOSE universe) | Postgres `ohlcv_daily` | — |
| VCI price board | Giao dịch khối ngoại per-ticker | Postgres `foreign_flows` | — |
| SSI iBoard | VN-Index/HNX/UPCOM/VN30/HNX30 OHLCV | Postgres `market_index_daily` | — |
| yfinance / CoinGecko | World indices, commodities, crypto, FX | Không lưu (real-time via tools) | — |
| CafeF events calendar | Sự kiện quyền, cổ tức, ĐHCĐ | Postgres `corporate_events` | — |
| Broker views (manual/scrape) | Nhận định CTCK, price target | Postgres `broker_views` | — |

**Priority khi trùng `financial_facts`:** vnstock > pdf (ON CONFLICT DO UPDATE).

---

## Postgres Schema (đầy đủ)

```sql
-- RAG / financial
financial_facts (ticker, period, report_type, metric_code, value, unit, source_file, source_page, source)
  UNIQUE (ticker, period, report_type, metric_code)

stock_prices (ticker, ngay, close_adj, volume)
  UNIQUE (ticker, ngay)

news_articles (url, title, body, source, published_at, tickers[], indexed_at)
  UNIQUE (url)

-- Market data (bài daily-brief)
ohlcv_daily (ticker, date, open, high, low, close, volume, ...)
  UNIQUE (ticker, date)

foreign_flows (ticker, date, buy_value, sell_value, net_value, market)
  UNIQUE (ticker, date)

market_index_daily (index_code, date, open, high, low, close,
                    change_pts, change_pct, matched_value, matched_volume, foreign_net)
  UNIQUE (index_code, date)

corporate_events (ticker, ex_date, event_type, ...)
  UNIQUE (ticker, ex_date, event_type)

broker_views (id, ticker_or_index, published_date, source, headline, target_price, ...)

securities (ticker, exchange, ...)
  UNIQUE (ticker)
```

---

## Dagster Pipeline (toàn bộ groups)

```
Group: ingestion (PDF BCTC)
  raw_pdf → parsed_doc → embeddings      → Qdrant hpg_b7_structural_meta
                       → financial_facts  → Postgres (source='pdf')

Group: news (mỗi 6h)
  news_raw → news_indexed  → Qdrant news_chunks
  news_purge               → xóa > 90 ngày (Chủ Nhật 02:00)

Group: vnstock
  vnstock_financials       → Postgres financial_facts (mùng 1 hàng tháng 01:00)
  vnstock_prices           → Postgres stock_prices (18:00 ngày thường)

Group: market_data
  market_index_daily_ingest → Postgres market_index_daily (18:00 ngày thường)
  global_quotes_ingest      → (không lưu, used by tools real-time)
  ohlcv_daily_ingest        → Postgres ohlcv_daily (18:30 ngày thường)

Group: market_brief
  07:00  corporate_events_ingest → Postgres corporate_events (weekdays)
  07:15  daily_brief             → info/DD_MM_YYYY.txt via market_brief_graph (weekdays)
  17:30  foreign_flows_ingest    → Postgres foreign_flows (weekdays, sau HoSE đóng)
```

**Sensor:** `minio_new_pdf_sensor` — phát hiện PDF mới/sửa/xóa trong MinIO mỗi 5 phút.

---

## RAG-Fusion Graph (LangGraph) — `rag/rag_fusion_graph.py`

```
decompose → multi_retrieve → rrf_fuse → analyze → report
```

| Node | Việc làm |
|---|---|
| `decompose` | LLM sinh N sub-queries từ câu hỏi gốc (default N=4) |
| `multi_retrieve` | Parallel: BM25 + vector hybrid (hpg_chunks) + news_chunks + Postgres facts + Tavily web |
| `rrf_fuse` | RRF merge tất cả kết quả → top K chunks, gắn `sources_used` |
| `analyze` | LLM đọc fused context → trả lời, gắn tag nguồn trong text |
| `report` | Format output + citation |

### Retrieval trong `multi_retrieve`

```
hpg_chunks:   BM25 (underthesea VN tokenize) + vector (Qdrant) → RRF → top 20
news_chunks:  vector search + DatetimeRange filter (30 ngày) → classify_sentiment() on-the-fly
Postgres:     query_postgres_facts() → financial_facts
Web:          Tavily.invoke(query) → 3 articles
```

### Sentiment trong news_chunks

Sentiment **không** được lưu vào Qdrant payload (quá đắt khi index hàng loạt).
Thay vào đó, `_retrieve_news()` gọi `classify_sentiment()` trên mỗi chunk vừa lấy về (~5 calls/query):

```
_retrieve_news() → top 5 payloads → classify_sentiment(payload["text"]) → tag vào chunk
→ "[TIN TỨC 2026-08-20 | sentiment: negative] Thép giảm mạnh (nguồn: cafef.vn)"
```

`analyze_node` thấy tag → tổng hợp được: "3/5 tin negative, 1 neutral, 1 positive".

---

## Sequential Agent Graph (Bài 22) — `agents/graph.py`

Dùng cho phân tích single-ticker (e.g. `python -m agents.run HPG`).

```
collect → analyze_technical → assess_risk → synthesize
```

| Node | Việc làm |
|---|---|
| `collect` | Parallel: get_historical_ohlcv + search_financial_news → lưu OHLCV CSV vào `outputs/agent_cache/` |
| `analyze_technical` | calculate_indicators từ CSV path → tech_signals string |
| `assess_risk` | Pure if/else (không gọi LLM): std > 4% → HIGH_VOLATILITY |
| `synthesize` | LLM via create_client(): nhận tech + risk + news → final report |

**State:** `agents/state.py::AgentState` — chỉ lưu paths, không lưu DataFrame.
**Entry:** `agents/run.py` — CLI, detect ticker tự động từ query.

---

## Market Brief Graph (Daily Brief) — `agents/market_brief_graph.py`

Tạo bản tin thị trường hàng ngày. Chạy 07:15 qua Dagster, hoặc manual:
`python agents/run_brief.py --date 2026-08-26`

```
collect_all (fan-out 4 threads) → compose_outlook (1 LLM call) → render_report (template)
```

### Node 1: collect_all — fan-out 4 sub-collectors song song (ThreadPoolExecutor)

| Sub-collector | Tools dùng | Dữ liệu lấy |
|---|---|---|
| `_collect_world()` | get_global_indices, get_commodities, get_vn_gold, get_crypto_prices, get_fx_rates | World indices, gold (XAU + SJC), oil (WTI/Brent), crypto, FX |
| `_collect_vn()` | query_index_latest, get_market_breadth, get_top_movers, get_foreign_flows, get_sector_performance | VN-Index (DB-first), breadth, movers, foreign flows, sectors |
| `_collect_news()` | search_news_by_text (Qdrant), get_broker_views, get_corporate_events | News headlines, broker views, upcoming events |
| `_collect_technical()` | get_historical_ohlcv (250 ngày), calculate_indicators, detect_candle_pattern, find_support_resistance | MA/RSI/MACD/ADX/Ichimoku, candle, support/resistance |

Thiếu data → `"(không có dữ liệu)"`, log vào `missing_fields`. Không để LLM tự điền.

VN-Index: ưu tiên `market_index_daily` DB → fallback `get_market_performance()` live. Tự tính streak (chuỗi tăng/giảm liên tiếp) + "hụt/vượt mốc" tâm lý từ high/low.

### Node 2: compose_outlook — 1 LLM call

Chỉ viết phần `🎯 NHẬN ĐỊNH` (2–3 đoạn narrative). **Không viết số liệu.**
Response bắt đầu bằng `###NHẬN ĐỊNH###`. `_strip_reasoning()` tách nội dung ra khỏi chain-of-thought.

### Node 3: render_report — Python template

File template: `agents/templates/market_brief.txt`.
Các section số liệu lấy từ state (data), không từ LLM. Output: `info/DD_MM_YYYY.txt`.

**State:** `agents/market_brief_state.py::MarketBriefState` — text strings only.

---

## Structured Planner (Bài 23) — `agents/planner.py`

Plan là JSON có schema, không phải prose. LLM → JSON → validate → execute hoặc retry.

```python
class Step: id, intent, executor, depends_on, expected_output
class Plan: steps: list[Step], budget_tokens: int
```

### `validate_plan(plan, registry)` — 5 checks:
1. Số steps ≤ MAX_STEPS (10)
2. budget_tokens ≤ MAX_BUDGET_TOKENS (20_000)
3. Mọi `depends_on` phải là step id hợp lệ
4. Mọi `executor` phải có trong registry
5. Không có circular dependency (DFS)

### `generate_plan(query, registry, client)`:
- Attempt 1: LLM sinh JSON plan
- Attempt 2 (nếu lỗi): gửi lại với error messages
- Fallback: `default_plan()` — sequential 4 bước cứng

---

## Tool Layer — `tools/`

### ToolResult contract (`tools/result.py`)
Mọi tool trả `ToolResult(status, data, message)`. Status values:
- `ok` — thành công
- `no_data` — không có dữ liệu (không phải lỗi)
- `upstream_error` — lỗi kết nối/server
- `rate_limited` — bị rate limit
- `invalid_input` — tham số sai

### TOOL_REGISTRY (`tools/registry.py`)
Metadata cho mọi tool: `version`, `timeout`, `cost_hint` (free/low/medium), `side_effect`.

Tools đã đăng ký:
```
get_realtime_price, get_realtime_price_intl, get_historical_ohlcv, get_historical_ohlcv_intl,
calculate_indicators, search_financial_news, analyze_market_sentiment,
get_market_performance, get_market_breadth,
get_global_indices, get_commodities, get_crypto_prices, get_fx_rates, get_vn_gold,
get_top_movers, get_foreign_flows, get_sector_performance,
detect_candle_pattern, find_support_resistance,
get_corporate_events, get_broker_views
```

### Price tools (`tools/price.py`) — dùng VciDirectProvider hoặc YFinanceProvider

| Function | Mô tả |
|---|---|
| `get_realtime_price(ticker)` | Giá hiện tại VN |
| `get_realtime_price_intl(ticker)` | Giá quốc tế (yfinance) |
| `get_historical_ohlcv(ticker, days)` | OHLCV lịch sử (VN hoặc quốc tế) |
| `calculate_indicators(df)` | MA20/50, RSI, MACD, ADX, Ichimoku, BB |
| `detect_candle_pattern(df)` | Doji/Marubozu/Hammer/Engulfing/... |
| `search_financial_news(query, days)` | Qdrant news_chunks |
| `analyze_market_sentiment(ticker)` | LLM-based sentiment (cost_hint=medium) |
| `get_market_performance(period, index)` | VN-Index performance |
| `get_market_breadth(market)` | Advances/declines/unchanged |
| `get_top_movers(by, n)` | Top movers by value/volume/change |
| `get_foreign_flows(days, as_of_date)` | Net foreign buy/sell từ foreign_flows DB |
| `get_sector_performance(period)` | Sector gainers/losers |

### Other tools

| Module | Tools |
|---|---|
| `tools/global_market.py` | get_global_indices, get_commodities, get_crypto_prices, get_fx_rates, get_vn_gold |
| `tools/index_db.py` | query_index_latest, query_index (market_index_daily) |
| `tools/levels.py` | find_support_resistance (pivot points, swing high/low) |
| `tools/foreign_flow_db.py` | query_market_foreign_net (foreign_flows table) |
| `tools/events_views.py` | get_corporate_events, get_broker_views |

### MCP Server (`tools/mcp_server.py`) — Bài 21

Expose 5 tools qua Model Context Protocol (FastMCP):
`get_price`, `get_ohlcv`, `get_indicators`, `search_news`, `get_market_sentiment`

Chạy: `python tools/mcp_server.py` hoặc inspect: `npx @modelcontextprotocol/inspector python tools/mcp_server.py`

---

## Chiến lược Chunking

| Collection | Chiến lược | Chunk size | Ghi chú |
|---|---|---|---|
| `hpg_b7_structural_meta` | structural + metadata | ~512 token | Dùng trong production |
| `hpg_fixed_512` | fixed 512 | 512 token | Baseline |
| `news_chunks` | title + body | N/A | Mỗi article = 1 chunk |

---

## Retrieval Methods (eval modes)

| `--retriever` | Mô tả |
|---|---|
| `vector` | Chỉ Qdrant vector search |
| `bm25` | Chỉ BM25 |
| `hybrid_weighted` | BM25 + vector, weighted sum fusion |
| `hybrid_rrf` | BM25 + vector, RRF fusion |
| `hybrid_rerank` | hybrid_weighted → CrossEncoder rerank |
| `rag_fusion` | Decompose → multi-retrieve → RRF → LangGraph (production) |
| `router_sql` | Classify → SQL agent hoặc hybrid_rerank |

---

## Router + SQL Agent

```
câu hỏi → classify (LLM) → label
  số_liệu     → SQL agent → financial_facts / stock_prices
  diễn_giải   → hybrid_rerank → RAG corpus
  cả_hai      → SQL + RAG → LLM combine
  ngoài_phạm_vi → từ chối
```

**Allowed tables:** `financial_facts`, `stock_prices`.

---

## Source Tags trong Context

| Tag | Nguồn | Ghi chú |
|---|---|---|
| `RAG corpus` | hpg_chunks (PDF embed) | Không có prefix trong raw text |
| `TIN TỨC YYYY-MM-DD \| sentiment: X` | news_chunks nội bộ | Sentiment classify lúc retrieve (không lưu Qdrant) |
| `GIÁ LỊCH SỬ` | Postgres financial_facts + stock_prices | vnstock hoặc PDF extract |
| `WEB` | Tavily real-time | Chưa kiểm chứng — LLM phải ghi `(Nguồn: Web)` |

---

## Eval

| File | Mô tả |
|---|---|
| `evals/golden_hpg.yaml` | Golden questions: table_lookup, text_interpretation, no_answer, out_of_scope, news |
| `evals/run.py` | Runner: model call → RAGAS score → regression check |
| `evals/eval_rag_fusion.py` | Eval riêng RAG-Fusion pipeline |
| `evals/eval_router.py` | Eval router accuracy |
| `evals/compare_architectures.py` | So sánh 3 kiến trúc: pure vector / hybrid+rerank / planner-based |

**Groups trong golden YAML:**
- `table_lookup` / `text_interpretation` → RAGAS (faithfulness, relevancy, precision, recall)
- `no_answer` / `out_of_scope` → refusal pass rate
- `news` → pass/fail dựa trên `sources_used` có `TIN TỨC`

**Threshold regression:** drop > 0.1789 (2×std từ noise measurement) → CI fail.

---

## Entry Points hiện tại đang chạy

| Script | Mục đích |
|---|---|
| `python agents/run_brief.py --date YYYY-MM-DD` | Chạy daily market brief thủ công |
| `python -m agents.run HPG` | Chạy sequential agent phân tích ticker |
| `python agents/run.py --graph-only` | Xuất sơ đồ graph |
| `python tools/mcp_server.py` | Khởi động MCP server |
| `python evals/compare_architectures.py` | So sánh 3 kiến trúc RAG |
| `dagster dev` | Khởi động Dagster UI + tất cả schedules |

**Dagster schedules đang active:**
- `05:30 * * *` — news_job (mỗi 6h)
- `0 1 1 * *` — vnstock_financials
- `0 18 * * 1-5` — vnstock_prices, market_index_daily_ingest
- `30 18 * * 1-5` — ohlcv_daily_ingest
- `0 7 * * 1-5` — corporate_events_ingest
- `15 7 * * 1-5` — daily_brief
- `30 17 * * 1-5` — foreign_flows_ingest

---

## Code không còn được gọi / chỉ dùng trong demo

| File | Tình trạng |
|---|---|
| `rag/demo_rag_fusion.py` | Demo script, không trong production flow |
| `rag/multi_query.py` | Utility được `rag_fusion_graph.py` import — vẫn active |
| `evals/debug_bm25.py` | Debug utility, không trong CI |
| `evals/compare_chunking.py` | Bài 7, không chạy thường xuyên |
| `evals/compare_embeds.py` | Bài 8, không chạy thường xuyên |
| `evals/compare_retrievers.py` | Bài 15, không chạy thường xuyên |
| `evals/eval_reranker.py` | Bài 16, không chạy thường xuyên |
| `evals/eval_news_quality.py` | Bài 19B, không chạy thường xuyên |
| `evals/eval_sentiment.py` | Bài 19B, không chạy thường xuyên |
| `scripts/reset_and_index.py` | One-shot setup, không tái dùng |
| `llm/demo.py` | Demo LLM factory, không production |
| `data/known_tickers_seed.py` | Seed script, chạy 1 lần |
| `ingest/extract_broker_views.py` | Manual scrape, không Dagster-ized |

**Lưu ý DB connection:** Hai module `core/db.py` và `data/db.py` cùng tồn tại với logic giống nhau. Phần lớn code mới dùng `core/db.py`. `data/db.py` vẫn được `tools/events_views.py` import.

---

## Changelog

| Bài | Thay đổi |
|---|---|
| 1–12 | Ingestion PDF, chunking, embedding, BM25, hybrid, reranker, facts SQL, news scraper |
| 13 | Dagster pipeline: ingestion_job, news_job, sensor MinIO |
| 14 | Router + SQL agent |
| 15–16 | RAG-Fusion (LangGraph): decompose → multi_retrieve → RRF → analyze |
| 17 | Tenant isolation |
| 18 | Router + SQL tích hợp vào rag_fusion |
| — | Thêm Dagster assets vnstock_financials + vnstock_prices |
| — | Phân biệt source tags: WEB vs TIN TỨC vs RAG corpus |
| — | Sentiment classify at retrieve time (không lưu Qdrant payload) |
| 19 | tools/price.py: get_realtime_price, get_historical_ohlcv, calculate_indicators, search_financial_news, analyze_market_sentiment |
| 19B | Thêm news sentiment eval + tools registry |
| 20 | ToolResult contract + TOOL_REGISTRY + MCP server (bài 21) |
| 21 | MCP server FastMCP expose 5 tools |
| 22 | agents/graph.py: sequential 4-node agent (collect→tech→risk→synthesize). AgentState. agents/run.py CLI |
| Daily brief p0–p3 | tools/global_market.py, market brief graph skeleton, collect_world + collect_vn + collect_technical |
| Daily brief p1 | HOSE universe, get_top_movers, get_market_breadth, seeds securities |
| Daily brief p2 | get_foreign_flows, get_sector_performance, foreign_flows table + ingest, Dagster asset |
| Daily brief p4 | calculate_indicators extension (ADX/Ichimoku/BB), detect_candle_pattern, find_support_resistance |
| Daily brief p5 | corporate_events table + scraper, broker_views table, get_corporate_events, get_broker_views |
| Daily brief p6 | market_brief_graph final assembly: compose_outlook, render_report, _strip_reasoning, template, Dagster schedule |
| 23 | agents/planner.py: Step/Plan schema, validate_plan (5 checks + DFS cycle detection), generate_plan (LLM + retry + fallback) |
