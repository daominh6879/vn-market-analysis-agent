.PHONY: up down test logs eval eval-baseline noise test-idempotent index migrate delete reconcile reconcile-fix migrate-quarantine quality-check quality-list migrate-facts extract-facts query-fact fetch-prices migrate-nguon fetch-financials fetch-financials-dry fetch-financials-schema pipeline-dev pipeline-ui eval-bm25 eval-bm25-vn eval-fusion eval-hybrid-rrf eval-hybrid-weighted eval-reranker demo-rag-fusion eval-rag-fusion eval-rag-fusion-run test-tenant migrate-readonly news-fetch-ticker news-backfill news-reindex test-sentiment mcp-server mcp-inspect test-tools test-chaos migrate-b28 test-b28 api-b28 test-b30 eval-b30 eval-b30-notes test-b31 api-b31 curl-stream-b31 ui-b31 ui-chainlit ui-react test-b32 test-b32-unit api-b32

up:
	docker compose up -d

down:
	docker compose down

test:
	pytest tests/ -v

logs:
	docker compose logs -f

eval:
	python evals/run.py --questions evals/golden_hpg.yaml

eval-baseline:
	python evals/run.py --questions evals/golden_hpg.yaml --save-baseline

eval-fast:
	python evals/run.py --questions evals/golden_hpg.yaml --skip-ragas

eval-ci:
	python evals/run.py --questions evals/golden_hpg.yaml --only-refusal --skip-ragas

noise:
	python evals/measure_noise.py --apply

noise-dry:
	python evals/measure_noise.py

test-idempotent:
	uv run pytest tests/test_idempotent.py -v -s

index:
	python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_structural --strategy structural

# Bài 10
migrate:
	python -c "from data.db import run_migration; run_migration('infra/migrations/001_documents.sql')"

delete:
ifndef DOC_ID
	$(error Usage: make delete DOC_ID=<doc_id> [COLLECTION=hpg_structural])
endif
	python data/delete.py --doc-id $(DOC_ID) --collection $(or $(COLLECTION),hpg_structural)

reconcile:
	python data/reconcile.py --collection $(or $(COLLECTION),hpg_structural)

reconcile-fix:
	python data/reconcile.py --collection $(or $(COLLECTION),hpg_structural) --fix

# Bài 11
migrate-quarantine:
	python data/quality.py --run-migration

quality-check:
ifndef FILE
	$(error Usage: make quality-check FILE=<path>)
endif
	python data/quality.py --file $(FILE)

quality-list:
	python data/quality.py --list-quarantine

# Bài 12
migrate-facts:
	python -c "from data.db import run_migration; run_migration('infra/migrations/003_financial_facts.sql')"

extract-facts:
ifndef FILE
	$(error Usage: make extract-facts FILE=outputs/2024/hpg_pymupdf.md [KY=2024] [LOAI=rieng_le])
endif
	python ingest/extract_facts.py --file $(FILE) --ky $(or $(KY),2024) --loai $(or $(LOAI),rieng_le)

extract-facts-dry:
ifndef FILE
	$(error Usage: make extract-facts-dry FILE=outputs/2024/hpg_pymupdf.md)
endif
	python ingest/extract_facts.py --file $(FILE) --ky $(or $(KY),2024) --loai $(or $(LOAI),rieng_le) --dry-run

query-fact:
ifndef MA
	$(error Usage: make query-fact MA=tong_tai_san KY=2024)
endif
	python ingest/extract_facts.py --file /dev/null --query $(MA) $(or $(KY),2024)

fetch-prices:
	python ingest/fetch_prices.py --ticker $(or $(TICKER),HPG) --from $(or $(FROM),2022-01-01) --to $(or $(TO),2024-12-31)

# Bài 12+ — vnstock Finance secondary ingest
migrate-nguon:
	python -c "from data.db import run_migration; run_migration('infra/migrations/004_nguon_column.sql')"

fetch-financials-schema:
	python ingest/fetch_financials.py --ticker $(or $(TICKER),HPG) --show-schema

fetch-financials-dry:
	python ingest/fetch_financials.py --ticker $(or $(TICKER),HPG) --period-from $(or $(FROM),2020) --period-to $(or $(TO),2024) --dry-run

fetch-financials:
	python ingest/fetch_financials.py --ticker $(or $(TICKER),HPG) --period-from $(or $(FROM),2020) --period-to $(or $(TO),2024)

# Bài 13
pipeline-dev:
	dagster dev -f pipeline/assets.py

pipeline-ui:
	@echo "Mở http://localhost:3000"

# Bài 14
eval-bm25:
	uv run python evals/run.py --retriever bm25 --collection hpg_structural --skip-ragas --out evals/bm25_raw.json

eval-bm25-vn:
	uv run python evals/run.py --retriever bm25 --collection hpg_structural --vn-tokenize --skip-ragas --out evals/bm25_vn.json

# Bài 15 — Hybrid Fusion
eval-fusion:
	uv run python evals/eval_fusion.py

eval-hybrid-rrf:
	uv run python evals/run.py --retriever hybrid_rrf --collection hpg_structural --vn-tokenize --skip-ragas --out evals/hybrid_rrf.json

eval-hybrid-weighted:
	uv run python evals/run.py --retriever hybrid_weighted --collection hpg_structural --vn-tokenize --skip-ragas --out evals/hybrid_weighted.json

# Bài 16 — Reranker
eval-reranker:
	uv run python evals/eval_reranker.py --collection hpg_structural --candidate-k 30 --skip-512 --out evals/reranker_results.json

# Bài 16b — RAG-Fusion (multi-query + RRF)
demo-rag-fusion:
	python rag/demo_rag_fusion.py

eval-rag-fusion:
	python evals/eval_rag_fusion.py

eval-rag-fusion-run:
	uv run python evals/run.py --collection hpg_b7_structural_meta --embed bge-m3 --retriever rag_fusion --skip-ragas

# Bài 17 — Tenant Isolation
migrate-readonly:
	python -c "from data.db import run_migration; run_migration('infra/migrations/004_readonly_role.sql')"

test-tenant:
	uv run pytest tests/test_tenant_isolation.py -v

run-dagster:
	dagster dev -f pipeline/assets.py

news-fetch-ticker:
ifndef TICKER
	$(error Usage: make news-fetch-ticker TICKER=HPG [DAYS=7])
endif
	python data/cafef_ticker_scraper.py --ticker $(TICKER)
	python data/tavily_news.py --ticker $(TICKER) --days $(or $(DAYS),7)
	python rag/news_index.py --index-all

news-backfill:
	@echo "Re-extract tickers for all articles then re-index to Qdrant"
	python data/news_scraper.py --backfill-tickers
	python rag/news_index.py --index-all

news-reindex:
	@echo "Reset indexed_at → re-embed all articles"
	python -c "from data.db import get_conn; conn=get_conn().__enter__(); conn.cursor().execute('UPDATE news_articles SET indexed_at=NULL'); conn.commit()"
	python rag/news_index.py --index-all

test-sentiment:
	pytest tests/test_sentiment.py -v

# Bài 21 — MCP server
mcp-server:
	python tools/mcp_server.py

mcp-inspect:
	npx @modelcontextprotocol/inspector python tools/mcp_server.py

test-tools:
	pytest tests/test_tools.py -v

test-chaos:
	pytest tests/test_tool_chaos.py -v

# Bài 28 — Conversation + Memory
migrate-b28:
	python -c "from data.db import run_migration; run_migration('infra/migrations/028_conversations.sql')"

test-b28:
	pytest tests/test_bai28_conversation.py -v -s

api-b28:
	python -m uvicorn api.main:app --reload --port 8028

# Bài 29 — Memory: quên đi (episodic memory)
migrate-b29:
	python -c "from data.db import run_migration; run_migration('infra/migrations/029_episodic.sql')"

test-b29:
	pytest tests/test_bai29_episodic.py -v -s

test-b29-load:
	pytest tests/test_bai29_episodic.py::test_load_20_episodes_no_context_bloat -v -s

# Bài 30 — Đo memory + test rò rỉ giữa người dùng
test-b30:
	pytest tests/test_memory_isolation.py -v

eval-b30:
	python evals/eval_memory_b30.py

eval-b30-notes:
	python evals/eval_memory_b30.py --update-notes

# Bài 31 — Streaming
test-b31:
	pytest tests/test_bai31_streaming.py -v -s

api-b31:
	python -m uvicorn api.main:app --reload --port 8031

curl-stream-b31:
ifndef CID
	$(error Usage: make curl-stream-b31 CID=<conversation_id> MSG="câu hỏi")
endif
	curl -N --no-buffer -s -X POST http://localhost:8031/conversations/$(CID)/messages/stream \
	  -H "Content-Type: application/json" \
	  -d "{\"user_id\":\"test\",\"message\":\"$(MSG)\"}"

ui-b31:
	@echo "Requires API on :8031 — run 'make api-b31' in another terminal first"
	.venv\Scripts\streamlit run ui/chat.py

ui-chainlit:
	@echo "Requires API on :8031 — run 'make api-b31' in another terminal first"
	.venv\Scripts\chainlit run ui/chainlit_app.py -w

ui-react:
	@echo "Requires API on :8031 — run 'make api-b31' in another terminal first"
	cd ui\react && npm run dev

# Bài 32 — Cache
test-b32:
	pytest tests/test_bai32_cache.py -v -s

test-b32-unit:
	pytest tests/test_bai32_cache.py -v -k "not real"

api-b32:
	python -m uvicorn api.main:app --reload --port 8032