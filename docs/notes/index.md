# NOTES — Kết quả & Quyết định

Ghi chép thực nghiệm, số đo, quyết định kỹ thuật. Không giải thích khái niệm — xem `EXPLAIN.md`.

> File này là index. Nội dung chi tiết ở `docs/notes/`.

---

## Progress tracker

- [x] Bài 4 — Eval baseline (refusal_pass_rate=0.80)
- [x] Bài 5 — Noise floor (std=0.0894, ngưỡng CI=0.1789)
- [x] Bài 6 — PDF parse comparison (pymupdf4llm + vie+eng)
- [x] Bài 7 — Chunking strategies (structural thắng, avg RAGAS 0.640)
- [x] Bài 8 — Embedding model selection (bge-m3 thắng, avg RAGAS 0.376)
- [x] Bài 9 — Idempotent index (uuid5 chunk IDs, delete-before-upsert)
- [x] Bài 10 — Xoá tài liệu & đối chiếu (soft_delete + reconcile verified)
- [x] Bài 11 — Cửa lọc chất lượng (in progress)
- [x] Bài 12 — Financial facts + SQL (postgres financial_facts)
- [x] Bài 13 — Pipeline Dagster (assets.py)
- [x] Bài 14 — BM25 + tách từ tiếng Việt (refusal 0.80→1.00, 6 ví dụ)
- [x] Bài 15 — So sánh 6 collections, chọn structural_meta (recall 0.524, MAP 0.255)
- [x] Bài 15 (fusion) — weighted_sum thắng hit@5 13/21 (+2 vs vector); candidate_k=30 giải quyết regression @20
- [x] Bài 16 — Reranker CrossEncoder: fusion_ws vẫn thắng (13/21); reranker 11/21 + 20s p95 — không deploy
- [x] Bài 16b — RAG-Fusion: multi-query N=4 recall@5 0.952 (+11% vs single 0.857); p95 8.1s vs 2.8s
- [x] Bài 17 — Tenant isolation: filter tại query time + cache key prefix (4 test xanh)
- [x] Bài 18 — Router + SQL agent: 10/10 attack blocked, router 30/30 = 100%, integration 4/4 (readonly role + timeout verified)
- [x] Bài 12B — News pipeline: 268 bài/4 nguồn indexed, eval_news_quality 9/9, sentiment 96.5%
- [x] Bài 19 — 3 tool giá chứng khoán: VnstockProvider + YFinanceProvider, CLI, 44 tests xanh
- [x] Bài 19B — search_financial_news + analyze_market_sentiment, few-shot vi, 60 tests xanh
- [x] Bài 20 — ToolResult hợp đồng lỗi: 5 status, không tool nào raise, 93 tests xanh
- [x] Bài 21 — MCP server: 5 tool exposed, FastMCP v1, get_indicators là composite tool; VCI provider tách providers.py; index aliases ^VNINDEX
- [x] Bài 22 — LangGraph agent: market query vs stock query; VNINDEX OHLCV via yfinance aliases
- [x] Bài 23 — Kế hoạch có schema: Step/Plan Pydantic, validate_plan 5 điều kiện, cycle detection, retry + fallback; 21 tests xanh
- [x] Bài 26 — Daily Brief Phase 2: khối ngoại (get_foreign_flows DB-first) + sector performance, 39 tests xanh
- [x] Bài 27 — Daily Brief Phase 5: 011/012 migrations, corporate_events scraper, broker_views LLM extract, 42 tests xanh
- [x] Bài 28 — Daily Brief Phase 6: market_brief_graph (LangGraph 3-node), template, run_brief CLI, 33 tests xanh
- [x] Bài 26 (arch) — So 3 kiến trúc: arch_a thắng 4/5 chỉ số; arch_c tệ nhất (compound=1, fail_rate=3.85%); chọn arch_a production
- [ ] Bài 28 (conv+memory) — hạ tầng chat: conversations/messages/user_memory tables; turn flow; LLM extractor confidence >= 0.7; supersede logic; 9 tests

## Pipeline hiện tại

**structural + bge-m3 + metadata (`hpg_b7_structural_meta`)**

## Per-lesson notes

| Bài | File | Tóm tắt |
|-----|------|---------|
| Setup | [setup.md](setup.md) | Docker bind mounts, quy tắc làm việc |
| eval | [collection-eval.md](collection-eval.md) | 6 collections × 3 retrievers; structural_meta wins (recall 0.524) |
| Bài 4 | [bai-4-eval-baseline.md](bai-4-eval-baseline.md) | refusal_pass_rate=0.800 |
| Bài 5 | [bai-5-noise-floor.md](bai-5-noise-floor.md) | std=0.0894, ngưỡng CI=0.1789 |
| Bài 6 | [bai-6-pdf-parse.md](bai-6-pdf-parse.md) | pymupdf4llm + vie+eng wins |
| Bài 7 | [bai-7-chunking.md](bai-7-chunking.md) | structural no-meta wins (avg 0.640) |
| Bài 8 | [bai-8-embedding.md](bai-8-embedding.md) | bge-m3 wins (avg 0.376, dims=1024) |
| Bài 9 | [bai-9-idempotent.md](bai-9-idempotent.md) | uuid5 chunk IDs, delete-before-upsert |
| Bài 10 | [bai-10-xoa-doi-chieu.md](bai-10-xoa-doi-chieu.md) | soft_delete + reconcile verified |
| Bài 11 | [bai-11-chat-luong.md](bai-11-chat-luong.md) | quality filter in progress |
| Bài 12 | [bai-12-facts-sql.md](bai-12-facts-sql.md) | financial_facts postgres, fetch prices |
| Bài 13 | [bai-13-pipeline-dagster.md](bai-13-pipeline-dagster.md) | Dagster assets pipeline |
| Bài 14 | [bai-14-bm25.md](bai-14-bm25.md) | BM25 raw 0.80 → VN tokenize 1.00 refusal; 6 ví dụ BM25 vs vector |
| Bài 15 | [bai-15-fusion.md](bai-15-fusion.md) | weighted_sum chọn (candidate_k=30): hit@5 13/21 (+2 vs vector), fusion@20 = vector@20 = 17/21 |
| Bài 16 | [bai-16-reranker.md](bai-16-reranker.md) | CrossEncoder thất bại: 11/21 hit@5 + p95=20s vs fusion 13/21 + 5s; structural table chunks không phù hợp cross-encoder |
| Bài 16b | [bai-16b-rag-fusion.md](bai-16b-rag-fusion.md) | RAG-Fusion recall@5 0.952 (20/21) vs single 0.857 (18/21); LangGraph 5-node; guard đầu tư hoạt động |
| Bài 17 | [bai-17-tenant-isolation.md](bai-17-tenant-isolation.md) | filter tại query time; cache key prefix; 4 test (3 bắt buộc + post-filter demo) |
| Bài 18 | [bai-18-router-sql.md](bai-18-router-sql.md) | router + SQL agent; readonly role; 10/10 attack blocked |
| Bài 12B | [bai-12b-news-pipeline.md](bai-12b-news-pipeline.md) | 268 bài/4 nguồn, news_chunks dim=1024, eval_news_quality 9/9 ✅, sentiment EN=96.5% VI=90% ✅ |
| Bài 19 | [bai-19-tools.md](bai-19-tools.md) | 3 tool giá: VnstockProvider + YFinanceProvider + CLI; 44 tests xanh |
| Bài 19B | [bai-19b-news-sentiment.md](bai-19b-news-sentiment.md) | search_financial_news + analyze_market_sentiment; few-shot vi; 60 tests xanh |
| Bài 20 | [bai-20-tool-result.md](bai-20-tool-result.md) | ToolResult 5 status; _map_upstream_error; registry.py; 93 tests xanh |
| Bài 21 | [bai-21-mcp-server.md](bai-21-mcp-server.md) | FastMCP server 5 tool; mcp<2 pinned; get_indicators composite; VCI refactor; VNINDEX aliases |
| Bài 22 | [bai-22-agent-graph.md](bai-22-agent-graph.md) | LangGraph 4-node; market vs stock query detection; VNINDEX via yfinance |
| Bài 23 | [bai-23-agent-planner.md](bai-23-agent-planner.md) | Step/Plan schema; validate_plan 5 điều kiện; DFS cycle detection; retry + fallback; 21 tests xanh |
| Bài 26 | [bai-26-daily-brief-p2.md](bai-26-daily-brief-p2.md) | Phase 2: get_foreign_flows DB-first (foreign_flows table); get_sector_performance; 39 tests xanh |
| Bài 27 | [bai-27-daily-brief-p5.md](bai-27-daily-brief-p5.md) | Phase 5: 011/012 migrations; corporate_events scraper; broker_views LLM extract; 42 tests xanh |
| Bài 28 | [bai-28-daily-brief-p6.md](bai-28-daily-brief-p6.md) | Phase 6: market_brief_graph LangGraph 3-node; template; run_brief CLI; 33 tests xanh |
| Bài 26 (arch) | [bai-26-compare-arch.md](bai-26-compare-arch.md) | arch_a thắng (quality=3.31, latency=5.9s, cost=$0.0008, failure=0%); arch_c tệ nhất (compound=1, +38% cost); production = arch_a |
| Bài 28 | [bai-28-conversation-memory.md](bai-28-conversation-memory.md) | conversations/messages/user_memory; LLM extractor confidence >= 0.7; supersede logic; 9 tests |
