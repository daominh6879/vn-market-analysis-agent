.PHONY: up down test logs eval eval-baseline noise test-idempotent index migrate delete reconcile reconcile-fix migrate-quarantine quality-check quality-list migrate-facts extract-facts query-fact fetch-prices migrate-nguon fetch-financials fetch-financials-dry fetch-financials-schema pipeline-dev pipeline-ui

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
