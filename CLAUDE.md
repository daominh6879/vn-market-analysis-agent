# CLAUDE.md — ai-engineer RAG project

## Project overview

RAG pipeline for Vietnamese financial documents (HPG BCTC). 43 lessons across 8 chapters building toward full retrieval + agent system.

**Stack:** Python · Qdrant · Postgres · Redis · MinIO · Ollama (local LLM) · RAGAS (eval) · Dagster · LangGraph · FastAPI · Streamlit

---

## Python scripts — never run yourself unless user ask

```
# Example pattern — ask user to run:
python rag/chunking.py outputs/hpg_pymupdf.md
python rag/index.py --input outputs/hpg_pymupdf.md --all-strategies
python evals/run.py --skip-ragas
```

Activate venv first: `.venv\Scripts\activate`

---

## Language rules — code & schema

**All code identifiers and database column names must be in English.** No exceptions.

- Variable names, function names, class names, module names → English
- Database table names, column names, migration files → English
- Dict keys, JSON fields, Pydantic field names → English
- Comments and docstrings → English preferred; Vietnamese allowed only in doc files (`docs/`)

---

## LLM usage rules

**Default model provider: DeepSeek.** `.env` has `LLM_PROVIDER=deepseek`. Do not hardcode any other provider.

**Always use the factory — never instantiate model clients directly.**

```python
# CORRECT
from llm import create_client
client = create_client()
response = client.generate(messages=[...], system="...", tools=[...])

# WRONG — never do this
from openai import OpenAI
client = OpenAI(api_key=..., base_url="https://api.deepseek.com")

from anthropic import Anthropic
client = Anthropic(api_key=...)
```

`create_client()` reads `LLM_PROVIDER` from env and returns the right `LLMClient`. Switching provider = one `.env` change, zero code change.

---

## Per-lesson completion rule

**Every bài is not done until:**
1. Run the real output (LLM + tools, no mocks)
2. Read the actual output — report, plan, tool result
3. Evaluate quality against the lesson's "Xong khi" checklist
4. Fix any bugs found (truncation, parse errors, wrong structure)
5. Re-run to confirm fix

Do NOT mark a bài complete after tests pass. Tests prove code runs. Running + evaluating proves it works correctly.

**What to evaluate per output type:**

| Output | Evaluate |
|--------|----------|
| LLM report | All sections present, citations on every claim, no truncation, risk_verdict valid enum |
| Plan JSON | steps count scales with query complexity, all executors in registry, no unknown depends_on, complex query ≥ medium ≥ simple steps |
| ToolResult | status matches scenario, data shape correct (columns, row count), error path returns no_data not crash |

---

## Documentation accuracy rule

**Never write assumptions into NOTES.md, EXPLAIN.md, or QA.md.** Every claim must have a verified source:

- Root cause identified → must be confirmed by checking code, DB, logs, or running a query.
- Numbers, counts, states → must come from actual output (script run, DB query, file read), not inferred.
- Before writing "X is not indexed / X is missing / X failed because Y" → verify it. Run the check, read the file, query Qdrant/Postgres, then write.

If verification requires running a script the user must run: ask first, wait for output, then write.

---

## Documentation files — when to update each

All files are in Vietnamese. Match existing tone and format.

### NOTES.md → `docs/notes/`
Root `NOTES.md` is an index. Per-lesson files in `docs/notes/`. Update after **every completed step**. Add:
- Experiment results (scores, tables, timings)
- Technical decisions and the reason (e.g. "chọn fixed_512 vì faithfulness cao nhất")
- Commands to reproduce the result
- Observations from script output

Do NOT add: concept explanations, "tại sao X hoạt động", architecture diagrams → those go to EXPLAIN.md.

### EXPLAIN.md — khái niệm & kiến trúc
Update when you learn or explain something conceptual. Add:
- How a tool/metric/algorithm works (RAGAS, chunking strategies, embedding)
- "Tại sao" questions — why a technique behaves a certain way
- Architecture diagrams (ASCII) and data flow
- Lessons and principles derived from experiments (e.g. "metadata chỉ có lợi khi index nhiều nguồn")

Do NOT add: raw scores, specific experiment runs, commands → those go to NOTES.md.

### BLOCKED.md — blockers
Update when you hit a blocker or resolve one:
- **Add** `[ ]` entry when discovering something that cannot proceed (missing API key, tool broken, needs manual step)
- **Check off** `[x]` when resolved, add one-line resolution note
- Include link/resource if relevant (e.g. where to get the API key)

Do NOT add: things that are "nice to have" or future improvements → those stay in NOTES.md or EXPLAIN.md.

### QA.md — câu hỏi & trả lời
Update when user asks a question worth preserving. Add:
- Questions the user asked mid-session that had non-obvious answers
- Edge cases discovered during debugging
- "What if..." questions and their answers

Format: `**Q:** ... / **A:** ...` with brief context. Do NOT add questions already answered by EXPLAIN.md.

### Other docs
- `so-tay-thuc-hanh.md` → index pointing to `docs/so-tay/` (chapters split per file)
- `docs/evals-notes.md` — eval system design and architecture notes
- `docs/run-explained.md` — line-by-line explanation of `evals/run.py`
