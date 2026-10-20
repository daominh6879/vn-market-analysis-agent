# Plan — Hidden Portfolio API: sổ giao dịch + AI tư vấn mua/bán

## Context
The user wants a **hidden** API (not in OpenAPI docs, no auth, runs locally) that:
1. Manages each user's portfolio: a cash ledger + buy/sell transactions → positions, average cost, P/L, cash balance.
2. Has an **AI judge** that reads the data (price/technicals, foreign flows, financials, market context) and **news**, then gives a buy/sell/hold recommendation for each holding, suggests new tickers to buy, flags portfolio risk, and summarises market context.

**No ML model.** The earlier LightGBM signal model (`signals/`, `trade_signals`, `signal_models`, training/scoring Dagster assets) is dropped from this plan. The LLM is the decision-maker, constrained by deterministic guardrails (cash, sellable quantity, lot size, max weight).

What exists today: `agents/intents/investment_case.py` (LLM buy/sell for ONE ticker, no portfolio), `agents/intents/news_sentiment.py` (news for a ticker), `news_articles` table (`url, title, body, source, published_at, tickers[]`, filled by `pipeline/assets_news.py`), `closing_commentary.gather_data` (market snapshot), `screening`/`breakout`/`technical` gathers, `tools/price.calculate_indicators`, `guardrails.disclaimer_for`. Nothing holds per-user holdings or cash.

Decisions taken with the user: separate FastAPI router with `include_in_schema=False`; holdings stored in Postgres per `user_id`; cash ledger built from transactions; AI-judged advice (no ML model); output = per-holding action + new ideas + portfolio risk + market context.

## Rules for the implementing agent (from repo CLAUDE.md — mandatory)
- Repo: `C:\Users\HungDao\RAG\ai-engineer`. Read `CLAUDE.md` first.
- **Never run Python scripts yourself**; ask the user to run them (`.venv\Scripts\activate` first).
- LLM only through `from llm import create_client` (provider DeepSeek from `.env`). Never instantiate OpenAI/Anthropic directly.
- All identifiers, table/column names, JSON keys **in English**. Vietnamese only in user-facing text and `docs/`.
- Any new dataset → Dagster asset + schedule (`default_status=RUNNING`) + bootstrap check in `data_bootstrap_sensor`. An empty run must **raise**. (This plan adds no new dataset; ledger tables are user-entered data.)
- Schema changes go only through numbered files in `infra/migrations/` (run via `scripts/migrate.py`).
- Data-gathering functions make no LLM calls (`gather_data` pattern); the LLM is called once at the end.
- Done = real run (real LLM + DB), output read and evaluated against the "Verification" section, bugs fixed, then re-run. Passing tests is not enough.
- After each phase: update `docs/notes/` (results, commands) in Vietnamese. Write only verified numbers.
- Surgical changes: do not refactor surrounding code; match the existing style.

## Design principle
**Python gathers facts and enforces limits; the LLM judges.**
- Python (`gather_data`) collects the evidence per holding and per candidate: P/L, indicators, flows, fundamentals, recent news.
- The LLM returns a **structured JSON verdict** per ticker (`action`, `confidence`, `reasons`, `cited_news_ids`) — it, not a rule table, decides BUY_MORE / HOLD / TAKE_PROFIT / CUT_LOSS / BUY_NEW.
- Python then **validates and clamps** the verdict: action in the enum; sells ≤ `sellable_qty`; buys ≤ cash and ≤ max single-ticker weight, rounded down to 100-share lots; every `cited_news_ids` must exist in the evidence passed in. Invalid output → one retry, then fall back to HOLD with reason `invalid_llm_output`.
- Quantities are computed by Python from the action + sizing constants, never taken from the LLM.

---

## Phase 1 — Ledger & positions (no LLM)
**Migration** `infra/migrations/039_portfolio.sql` (already exists in the working tree — keep as is):
- `portfolio_transactions(id SERIAL PK, user_id TEXT, tenant_id TEXT DEFAULT 'default', txn_date DATE, kind TEXT CHECK IN ('deposit','withdraw','buy','sell','dividend','fee'), ticker TEXT NULL, quantity BIGINT NULL, price NUMERIC NULL, fee NUMERIC DEFAULT 0, tax NUMERIC DEFAULT 0, amount NUMERIC, note TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW())` + index `(user_id, txn_date)`.
- No separate holdings table: positions are derived from transactions (single source of truth).

**Module** `portfolio/ledger.py`:
- `add_transaction(...)` validates: ticker exists in `securities`; buy needs enough cash; sell needs enough **sellable** quantity (VN T+2: shares bought < 2 trading sessions ago cannot be sold; calendar from VNINDEX dates in `market_index_daily`); quantity is a multiple of 100 for HOSE/HNX. Default fee from env `PORTFOLIO_FEE_PCT` (e.g. 0.15%); sell tax 0.1%.
- `get_positions(user_id)` → per ticker: qty, sellable_qty, avg_cost (weighted, fees included), last price (latest `ohlcv_daily.close`), market value, unrealized P/L, realized P/L, weight, sector.
- `get_cash(user_id)` → cash balance, total deposited, NAV, total return.
- Uses `core.db.get_conn`.

**API** `api/portfolio.py`: `APIRouter(prefix="/portfolio", tags=["portfolio"], include_in_schema=False)`; mounted in `api/main.py`; not listed in the docstring endpoint list.
- `POST/GET /portfolio/{user_id}/transactions`, `DELETE /portfolio/{user_id}/transactions/{id}`
- `GET /portfolio/{user_id}/positions`, `GET /portfolio/{user_id}/cash`

## Phase 2 — Evidence gathering (no LLM)
**Module** `portfolio/evidence.py`: `gather_evidence(user_id) -> dict`
- Positions + cash (Phase 1).
- Per holding (and per candidate ticker):
  - technicals: `technical.gather_data(ticker, ...)` + `tools/price.calculate_indicators` (returns 5/20/60, RSI14, MACD, distance to MA20/50/200, volume vs MA20, 52-week range, support/resistance);
  - foreign flows: net value over 5/20 sessions from `foreign_flows`;
  - fundamentals: latest quarterly `financial_facts` ratios (visible only 45 days after quarter end);
  - **news**: last N days (env `PORTFOLIO_NEWS_DAYS`, default 14) from `news_articles WHERE ticker = ANY(tickers)`, top K by recency (default 8), each as `{id, published_at, source, title, snippet}` (body truncated). Each article gets a stable id so the LLM can cite it.
- Market context: `closing_commentary.gather_data(None, "")` + recent market-wide news (articles with empty `tickers`).
- **Candidates for new ideas** (the LLM has no model score to rank by, so pre-filter deterministically): liquid tickers (20-session average value ≥ floor), not already held, from sectors where portfolio weight < X%, ranked by a simple screen (reuse `screening`/`breakout` gathers) and capped at ~5 to keep the prompt small.
- Risk metrics: largest-ticker weight, largest-sector weight, cash %, portfolio beta vs VNINDEX (from `ohlcv_daily`).
- If a holding has no news in the window, say so explicitly in the evidence (`"news": []`) so the LLM does not invent any.

## Phase 3 — AI judge & advisor
**Module** `portfolio/advisor.py`:
- `judge(evidence) -> list[dict]` — one `create_client().generate(...)` call (or one per ticker if the prompt gets too large) with a system prompt that:
  - states the role (Vietnamese retail-investor adviser) and that it must use **only** the evidence provided;
  - requires strict JSON per ticker: `{ticker, action, confidence (0-1), reasons:[str], cited_news_ids:[int], risks:[str]}`;
  - allowed actions: `BUY_MORE`, `HOLD`, `TAKE_PROFIT`, `CUT_LOSS` for holdings; `BUY_NEW`, `SKIP` for candidates;
  - requires each reason to cite a number from the data or a news id; no price targets without data.
- `apply_limits(verdicts, evidence) -> list[dict]` — deterministic clamp (constants at the top): sell qty ≤ `sellable_qty` (T+2); buy qty = min(cash × per-trade %, room to max single-ticker weight) rounded down to 100-share lots; zero cash or zero sellable → downgrade action to HOLD with reason code `no_cash` / `not_sellable`; drop uncited/unknown news ids.
- `advise(user_id) -> dict`:
  1. `gather_evidence`; 2. `judge`; 3. `apply_limits`; 4. one more LLM call (or the same one) for the narrative slots (MARKET, per-holding RATIONALE, NEW_IDEAS, RISK) using the slot pattern; Python assembles the Markdown in a fixed structure. `guardrails.disclaimer_for({"advice"})` appended, then `scrub_internal`. The endpoint ignores `STRICT_NEUTRAL` because it exists to give advice.
  - Returns `{report_md, actions:[{ticker, action, quantity, price_ref, confidence, reasons, cited_news:[{id,title,url}]}], new_ideas:[...], risk:{...}, as_of}`.
- Guard against thinking leaks (see existing DeepSeek handling): use `strip_thinking` / `extract_report` from `agents/intents`.

**API**: `POST /portfolio/{user_id}/advise`.

## Phase 4 — TCBS sync (read-only, done)
`portfolio/tcbs.py` + `POST /portfolio/{user_id}/tcbs/sync`: mirrors TCBS holdings + cash into the ledger (see `docs/notes/portfolio-advisor.md`). No order endpoints on purpose.

## Dropped from the previous plan
`infra/migrations/040_trade_signals.sql`, `041_proprietary_flows_breakout_signals.sql` (only if they exist solely for this model — check before deleting), `signals/`, `pipeline/assets_signals.py`, `tests/test_signals_*.py`, `tests/test_backtest_rules.py`, `lightgbm` in `requirements.txt`, the `signal_model`/`trade_signals_daily` Dagster jobs and their bootstrap checks, `GET /portfolio/signals`. These are untracked/modified in the working tree today; **ask the user before deleting** any of them (041 and the `breakout` tests may serve other features).

## Critical files
New: `portfolio/{__init__,ledger,evidence,advisor}.py`, `api/portfolio.py`, `tests/test_portfolio_ledger.py`, `tests/test_portfolio_evidence.py`, `tests/test_portfolio_advisor.py`.
Edited: `api/main.py` (include router).
Existing, kept: `infra/migrations/039_portfolio.sql`.

Reused: `core.db.get_conn`, `llm.create_client`, `agents/guardrails.disclaimer_for/scrub_internal`, `agents/intents/closing_commentary.gather_data`, `agents/intents/technical.gather_data`, `agents/intents/news_sentiment._fetch_news_text` (reference for the news query), `agents/intents.extract_slot/strip_thinking/extract_report`.

## Verification (the user runs the scripts; Claude reads the output)
1. `python scripts/migrate.py`, confirm `portfolio_transactions` exists.
2. Unit tests: `pytest tests/test_portfolio_ledger.py tests/test_portfolio_evidence.py tests/test_portfolio_advisor.py`. Coverage:
   - ledger: avg cost with fees, sell tax, T+2 blocking, buy rejected when cash is short, 100-share lot rounding;
   - evidence: news window filter, empty-news case, no look-ahead on `financial_facts` (45-day lag);
   - advisor (LLM stubbed only in unit tests): invalid JSON → retry → HOLD fallback; invalid action enum rejected; unknown `cited_news_ids` dropped; sell clamped to `sellable_qty`; buy clamped to cash / max weight / lots.
3. Run uvicorn; check `/docs` does **not** show `/portfolio`. Curl: deposit 100M → buy HPG 1000 → positions/cash correct; sell on T+0 → 400.
4. `POST /portfolio/{user}/advise` with real DeepSeek and real DB. Read the output and evaluate:
   - all sections present (market, per-holding, new ideas, risk), no truncation, disclaimer present;
   - every claim cites a number from the data or a real news id; no invented news (cross-check each cited id/title against `news_articles`);
   - actions valid enum, quantities in lots of 100, within cash and sellable qty;
   - a holding with no news does not get news-based reasons;
   - recommendation is consistent with the evidence (e.g. a −10% holding with broken trend and negative news is not BUY_MORE without justification);
   - no thinking-leak text in `report_md`.
   Fix and re-run; record verified results in `docs/notes/portfolio-signals.md` (rename to `portfolio-advisor.md` if the signals content is removed).

## Implementation order
Phase 1 → test with the user → Phase 2 → Phase 3. Each phase is a separate commit. Phase 0 (history-depth check) is no longer needed.
