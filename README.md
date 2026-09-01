# AI Engineer — Vietnamese Financial RAG System

RAG pipeline for Vietnamese financial documents (BCTC). Stack: Python · Qdrant · PostgreSQL · Redis · MinIO · Ollama · Dagster · LangGraph · FastAPI · Streamlit/Chainlit/React.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | |
| [uv](https://docs.astral.sh/uv/) | latest | preferred package manager |
| Docker Desktop | 4.x+ | for infra services |
| Node.js | 18+ | React UI only |
| [Ollama](https://ollama.com) | latest | local LLM + embeddings |

---

## 1. Clone & configure

```bash
git clone <repo-url>
cd ai-engineer
cp .env.example .env   # if .env doesn't exist — fill in API keys
```

Key `.env` values to set:

```env
POSTGRES_USER=admin
POSTGRES_PASSWORD=secret
POSTGRES_DB=ragdb

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

LLM_PROVIDER=deepseek          # deepseek | anthropic | ollama | openai
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # optional fallback

OLLAMA_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=bge-m3
```

---

## 2. Install Python dependencies

```bash
uv sync
# or
pip install -r requirements.txt
```

Activate venv:

```bash
# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

---

## 3. Start Docker services

```bash
docker compose up -d
```

This starts:

| Service | Port | UI |
|---------|------|----|
| Qdrant (vector DB) | 6333 | http://localhost:6333/dashboard |
| PostgreSQL | 5432 | — |
| Redis | 6379 | — |
| MinIO | 9000 (API) / 9001 (console) | http://localhost:9001 |

Verify all running:

```bash
docker compose ps
```

---

## 4. Pull Ollama models

```bash
ollama pull qwen3:8b
ollama pull bge-m3
```

Ollama must be running (`ollama serve` or the desktop app).

---

## 5. Run migration (first run only)

```bash
python scripts/migrate.py
```

This runs 7 steps end-to-end:

| Step | What | Provider / source |
|------|------|-------------------|
| 1 | SQL migrations | `infra/migrations/*.sql` — all tables, indexes, roles |
| 2 | Seed securities | ~400 HOSE tickers → `securities` table |
| 3 | MinIO setup | Create bucket `bctc-reports` + upload 6 BCTC PDFs |
| 4 | Financial facts | vnstock Finance API → `financial_facts` (HPG/VCB/FPT, 2020–2025) |
| 5 | OHLCV + foreign flows | Fireant (primary) → KBS → VCI fallback, 1-year backfill → `ohlcv_daily` + `foreign_flows` |
| 6 | Market index | SSI iBoard, 365 days → `market_index_daily` (VNINDEX/HNX/UPCOM/VN30/HNX30) |
| 7 | Audit | `scripts/audit_db.py` — verify all tickers have sufficient data |

Options:

```bash
python scripts/migrate.py --dry-run              # preview plan, no changes
python scripts/migrate.py --skip-minio           # skip MinIO/PDF step
python scripts/migrate.py --skip-securities      # skip securities seed
python scripts/migrate.py --skip-market-data     # schema only, skip steps 4-6
python scripts/migrate.py --skip-audit           # skip final audit
```

---

## 6. Index BCTC PDFs into Qdrant

```bash
python scripts/reset_and_index.py
```

This parses HPG 2024 + 2025 PDFs and indexes into multiple Qdrant collections (fixed/structural/hierarchical, with/without metadata).

Options:

```bash
python scripts/reset_and_index.py --skip-parse  # if outputs/ markdown already exists
python scripts/reset_and_index.py --dry-run      # preview plan only
```

---

## 7. Start services

### FastAPI backend

```bash
uvicorn api.main:app --reload --port 8031
```

Endpoints: `GET /health`, `POST /conversations`, `POST /conversations/{id}/messages/stream`, …

### UI options (pick one)

**Streamlit:**
```bash
streamlit run ui/chat.py
# Open http://localhost:8501
```

**Chainlit:**
```bash
chainlit run ui/chainlit_app.py -w
# Open http://localhost:8000
```

**React (requires Node.js):**
```bash
cd ui/react
npm install
npm run dev
# Open http://localhost:5173
```

### Dagster pipeline UI

```bash
dagster dev -f pipeline/assets.py
# Open http://localhost:3000
```

---

## 8. Daily data refresh

After market close (18:30+ ICT):

```bash
python scripts/daily_ingest.py
```

Or via Dagster schedule in the pipeline UI.

---

## Architecture overview

```
PDF reports ──► MinIO (storage)
                │
                ▼
            pymupdf4llm (parse)
                │
                ▼
            Qdrant (vector index)    ◄── bge-m3 embeddings (Ollama)
                │
PostgreSQL ◄────┤ financial_facts, ohlcv_daily, news_articles, …
                │
            LangGraph agent (RAG + tools)
                │
            FastAPI (REST + SSE streaming)
                │
            UI (Streamlit / Chainlit / React)
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connection refused` on port 5432/6333 | `docker compose up -d` |
| `minio.error.S3Error` | Check MinIO running: `docker compose ps minio` |
| `ollama: connection error` | Start Ollama: `ollama serve` |
| Migrations fail on `rag_readonly` role | Run `004_readonly_role.sql` as DB superuser |
| `ModuleNotFoundError` | Activate venv + `uv sync` |

---

## Makefile shortcuts

```bash
make up              # docker compose up -d
make down            # docker compose down
make test            # pytest tests/
make eval            # run RAGAS eval
make api-b31         # start FastAPI on :8032
make ui-react        # start React UI
make pipeline-dev    # start Dagster
make ingest-daily    # daily market data refresh
make audit           # DB completeness audit
```

Full list: `make help` or see `Makefile`.
