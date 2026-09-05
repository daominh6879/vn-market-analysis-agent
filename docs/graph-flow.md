# Agent Graph Flow

## LLM-on-top turn entry (stream_turn)

```mermaid
flowchart TD
    UM([User Message]) --> IT{graph\ninterrupted?}

    IT -->|yes — fresh clarify interrupt| RESUME["Command(resume=user_message)\n→ graph continues from clarify_node"]
    RESUME --> GOUT

    IT -->|yes — stale (>10 min)| ORPHAN["orphan thread_id\n(abandon abandoned clarify)\nroute fresh"]
    ORPHAN --> LLM

    IT -->|no| LLM["llm_route(query, history, system_prompt,\n  last_intent, last_subject)\nSingle LLM call with 3 tools:\n• needs_agent_run(intent, ticker, query, reason)\n• direct_reply(text, reason)\n• out_of_scope(text, reason)\nlast_intent/last_subject injected for follow-ups"]

    LLM -->|needs_agent_run (valid intent)| PRE["Build state\nintent + ticker pre-set\nticker validated vs VN universe\nclassify_node skips LLM"]
    LLM -->|direct_reply| VERIFY{"re-classify\nwith history"}
    VERIFY -->|financial| PRE
    VERIFY -->|social| TEXT([Stream tool text\nno graph invoked])
    LLM -->|out_of_scope| OOS([Stream decline text\ncrypto/foreign/forex — no graph])
    LLM -->|no / invalid tool call| FALLBACK["re-classify\nwith history"]
    FALLBACK -->|agent| PRE
    FALLBACK -->|social / empty| TEXT2([Stream text or short error\nno graph invoked])
    LLM -->|exception| ERR([type=text\nshort Vietnamese error\nno graph])

    PRE --> GRAPH["Graph.invoke(state)"]
    GRAPH --> GOUT{graph\nresult}

    GOUT -->|_cache_hit| CACHED([Stream cached report])
    GOUT -->|clarify interrupt| CLARIFY([Stream clarification question\ngraph stays interrupted])
    GOUT -->|report| RPT([Stream report to user])
```

### Routing decision logic

| LLM action | Meaning | Result |
|---|---|---|
| calls `needs_agent_run` (valid intent) | Financial data needed | `type=agent` → graph |
| calls `direct_reply` | Claimed social turn | re-classify w/ history; financial → agent, else text |
| calls `out_of_scope` | crypto / foreign stock / non-VND forex | `type=text` → stream decline, no graph |
| no / invalid tool call | LLM bypassed tools | re-classify w/ history → agent / text / error |
| exception | LLM failed | `type=text` short error — **never `market_brief`** |

`needs_agent_run` `ticker` is validated against the active VN universe (`core.tickers.get_tickers()`)
before it enters the graph — company names, foreign codes, and market indices drop to `""` and the
graph's clarify/fan-out path re-resolves the subject. The router LLM handles market queries via
`market_brief`/`macro_sector` intent, not via a hardcoded market-index whitelist.

Follow-ups: `last_intent` / `last_subject` (read from the checkpointer state of the prior turn) are
appended to the router system prompt, so a bare continuation ("phân tích sâu hơn") inherits the prior
turn's subject instead of guessing from truncated history.

## Graph internals

```mermaid
flowchart TD
    IN([User query\nintent may be pre-set by llm_route]) --> classify_node

    classify_node["classify_node\n• intent pre-set → pass through (no LLM)\n• intent absent → llm_classify()"]

    classify_node --> check_conversation{intent?}

    check_conversation -->|conversation| END0([END — stream_turn handles streaming])
    check_conversation -->|out_of_scope| node_out_of_scope
    check_conversation -->|else| check_cache_node

    node_out_of_scope["node_out_of_scope\nfixed decline text — no gather, no LLM"] --> END1([END])

    check_cache_node["check_cache_node\nRedis single tier\nkey = (tenant_id, intent, ticker, scope, prompt_version, model_version)\nscope = question-hash for _ALWAYS_SCOPE_INTENTS\nuses query when original_query names no ticker"]
    check_cache_node -->|hit| END2([END — _cache_hit=True, report=cached])
    check_cache_node -->|miss| clarify_node

    clarify_node["clarify_node\n① detect_ambiguity — ticker/intent missing?\n② yes → interrupt() — wait for user answer\n③ merge answer → re-classify\n④ no ambiguity → pass through"]
    clarify_node --> route_check{route after\nclarify}

    route_check -->|out_of_scope| node_out_of_scope
    route_check -->|conversation| cache_save_node
    route_check -->|market_brief| node_market_brief
    route_check -->|"leaf intent + ticker"| fast_node
    route_check -->|"complex intent / no ticker"| decompose_node

    node_market_brief["node_market_brief\nbuild_brief_graph — global + VN data"] --> cache_save_node

    fast_node["build_single_subtask_node\nWrap intent+ticker → 1 sub_task\nno LLM call"] --> run_subqueries_node

    decompose_node["decompose_node\nLLM tool-call decomposition\n→ N sub_tasks [{intent, tickers, question}]"] --> run_subqueries_node

    run_subqueries_node["run_subqueries_node\nFor each sub_task (intent pre-classified):\n  gather_data(ticker, question) — NO LLM\n  fan-out per ticker in parallel (ThreadPoolExecutor + timeout)\n  unknown intent → log+trace, fallback macro_sector\nCollect sub_results, set sub_results_empty_ratio"]
    run_subqueries_node --> replan_check{">50% empty\nAND not replanned?"}

    replan_check -->|"yes — replan (≤1)\nfolds replan_note"| decompose_node
    replan_check -->|no| approval_check{human_approval?}

    approval_check -->|True| request_approval
    approval_check -->|False| synthesize_final

    request_approval["request_approval\ninterrupt() — human reviews\n{ticker, risk, signals, news}"]
    request_approval -->|approved| synthesize_final
    request_approval -->|rejected| END3([END — error: rejected_by_user])

    synthesize_final["synthesize_final\nSingle LLM call over sub_results\nMarkdown report (STRICT_NEUTRAL aware)\nfolds critique_feedback on retry\ntracks report_candidates"]
    synthesize_final --> critique_report_node

    critique_report_node["critique_report_node\nLLM self-check vs checklist\n→ {pass, feedback}\ntracks critique_results\nkeeps best report when retries exhausted"]
    critique_report_node --> critique_check{pass?}

    critique_check -->|"pass"| cache_save_node
    critique_check -->|"fail & attempts ≤ MAX_CRITIQUE (1)"| synthesize_final
    critique_check -->|"fail & attempts > MAX_CRITIQUE"| cache_save_node

    cache_save_node["cache_save_node\nPersist report to Redis"] --> END4([END — report in state])
```

Budget guard: `route_after_subqueries` and `route_after_critique` short-circuit first when the turn
has hit `MAX_TURN_LLM_CALLS` (default 8) or `MAX_TURN_SECONDS` (default 180) — no re-plan/retry once
the budget is gone.

## Cache design (single-tier Redis)

Key model: `(tenant_id, intent, ticker, scope, prompt_version, model_version)` — **no full question text**.

Same intent+ticker (+scope where applicable) always returns the same cached answer, cross-conversation.

`_ALWAYS_SCOPE_INTENTS` (`macro_sector`, `rag_qa`, `valuation`, `screening`, `breakout_scan`)
add an 8-char question-hash `scope` to the key — for these, ticker alone does not specify the
query (e.g. "P/E HPG" ≠ "ROE HPG"). All other intents use `scope=""` (ticker fully differentiates).

`check_cache_node` uses `original_query` (verbatim) only when it names a concrete ticker; a ticker-less
follow-up ("phân tích sâu hơn") keys off the router's self-contained `query` so the sector/subject is
part of the key and can't collide with another conversation's cached report.

Ticker extraction for the key is shared with the graph (`core.tickers.raw_tickers` / `extract_tickers`):
uppercase tokens matched first, stopword-filtered (ROE/EPS/PE/PB…), universe-filtered — one helper,
no duplicate regex.

### Per-intent TTL

| Intent | Market hours | Off-hours |
|---|---|---|
| `price_action` | 60 s | 300 s |
| `technical_analysis` | 300 s | 1800 s |
| `news_sentiment` | 600 s | 3600 s |
| `macro_sector` | 600 s | 3600 s |
| `market_brief` | 120 s | 1800 s |
| `investment_case` | 1800 s | 86400 s |
| `rag_qa` | 3600 s | 86400 s |
| `valuation` | 3600 s | 86400 s |
| `screening` | 300 s | 3600 s |
| `breakout_scan` | 120 s | 3600 s |

Market hours: Mon–Fri 09:00–14:45 VN time (UTC+7).

`conversation` intent → never cached. `out_of_scope` → not cached (no agent result).

## gather_data dispatch (run_subqueries_node)

| Intent | gather_data source |
|---|---|
| `price_action` | `intents/price_action.gather_data` |
| `technical_analysis` | `intents/technical.gather_data` |
| `news_sentiment` | `intents/news_sentiment.gather_data` |
| `macro_sector` | `intents/macro_sector.gather_data` |
| `investment_case` | `intents/investment_case.gather_data` |
| `screening` | `intents/screening.gather_data` |
| `rag_qa` | `rag/qa.retrieve_only` |
| `valuation` | `intents/fundamentals.gather_data` |
| `breakout_scan` | `intents/breakout.gather_data` |
| `market_brief` | static placeholder |

Fan-out applies for `price_action`, `technical_analysis`, `investment_case`, `breakout_scan`:
when a sub-task has multiple tickers, each is fetched in parallel (`ThreadPoolExecutor`) with a
per-fetch timeout (`GATHER_TIMEOUT_SECONDS`, default 30) and merged in input order.

An unknown intent from `decompose_node` is logged + traced (`gate:gather_unknown_intent`) and falls
back to `macro_sector` instead of silently producing an empty sub-result.

## LLM call count per turn

Base counts (critique passes on first try, no re-plan):

| Path | LLM calls |
|---|---|
| Direct reply (social) | 1 (`llm_route`) |
| Simple query (specific ticker, leaf intent) | 1 (`llm_route`) + 1 (`synthesize_final`) + 1 (`critique_report_node`) = **3** |
| Complex query (no clarify) | 1 (`llm_route`) + 1 (`decompose_node`) + 1 (`synthesize_final`) + 1 (`critique_report_node`) = **4** |
| Complex query (with clarify) | +1 (clarify re-classify) = **5** |

Bounded extra calls (each capped at 1, and blocked once the turn budget is exhausted):

- **Self-critique retry** — `critique_report_node` returns `pass=false` → re-run `synthesize_final` once with `critique_feedback` folded in (`MAX_CRITIQUE=1`).
- **Re-plan** — `run_subqueries_node` reports >50% empty/error sub-results → re-run `decompose_node` once with a `replan_note` (`replan_attempted` guard).

Simple path: `price_action`, `technical_analysis`, `news_sentiment`, `rag_qa`, `valuation`, `screening`, `breakout_scan` — only when a specific ticker is identified by `llm_route`.

Complex path (always decompose): `market_brief`, `investment_case`, `macro_sector`, or any intent with no ticker.

No per-sub-query classification — intent and tickers are pre-set by `decompose_node` tool calls (complex path) or by `llm_route` directly (simple path).

## Agent-style loops (bounded)

Two self-correcting loops added on top of the fixed DAG, both capped so cost stays bounded:

| Loop | Trigger | Action | Cap |
|---|---|---|---|
| **Re-plan** | `sub_results_empty_ratio > 0.5` (gather returned empty/error strings, e.g. ticker miss, tool error) | back to `decompose_node` with a `replan_note` ("thử câu hỏi khác") so different sub-questions are produced | 1 (`replan_attempted`) |
| **Self-critique** | `critique_report_node` flags report missing citation / truncated / off-topic | back to `synthesize_final` with `critique_feedback` folded into the prompt | 1 (`MAX_CRITIQUE`) |

When retries are exhausted, `critique_report_node` keeps the best `report_candidates` entry (a passing
draft if any, else the first draft) so a worse retry never overwrites a better first version.

`_is_empty_result` treats an empty string **and** error stubs (`lỗi:`, `Không có dữ liệu`, `ticker không được rỗng`) as unusable — so an error string no longer counts as "data present".

## Key architecture decisions

| Decision | Reason |
|---|---|
| Three tools (`needs_agent_run` + `direct_reply` + `out_of_scope`) | No free-form escape hatch — LLM makes explicit structured choice. `out_of_scope` gives foreign/crypto/forex a clean decline instead of a fabricated VN-intent report. |
| `tool_choice="auto"` not `"required"` | `required` forces tool call for pure social turns → LLM calls `needs_agent_run` with reconstructed context → wrong. `auto` lets LLM reply freely for social, which we treat as `type=text`. |
| No/invalid-tool-call fallback → re-classify with history | The classifier re-resolves intent + ticker with conversation history, so an implicit follow-up still resolves its subject. A social turn returns its free text; a failed classify returns a short error. |
| Exception fallback → `type=text` short error | LLM failure yields a short Vietnamese apology, never a full `market_brief` run (a greeting during a provider outage must not trigger the whole market graph). |
| `needs_agent_run` ticker validated vs VN universe | LLM ticker field can be a company name, "NGANHANG", or a foreign code. Universe-only validation drops those to `""`; the graph re-resolves. No hardcoded market-index whitelist — the router LLM handles market queries via intent. |
| `last_intent` / `last_subject` injected into router prompt | Bare follow-ups ("phân tích sâu hơn") need the prior turn's subject; 120-char truncated assistant history can't guarantee it. Explicit signal beats prompt-engineering around truncation. |
| Assistant history truncated to 120 chars in `llm_route` | Full reports in history let LLM answer new ticker queries from stale data. 120 chars = header only (confirms topic, not data). |
| Stale interrupt orphaned (thread_id swap) | A clarify interrupt left >10 min (user abandoned it) must not swallow every later message. A fresh `thread_id` orphans it and the turn routes normally. |
| Intent-level cache key (no question text) | Same intent+ticker = same data shape = same answer. Avoids cache misses from paraphrase. Ticker-less follow-ups key off the self-contained `query`. |
| No per-sub-query `classify_hybrid` | `decompose_node` uses tool calling — LLM returns structured `{intent, tickers, question}` directly. Saves N LLM calls per turn. |
| `classify_node` skips LLM when intent pre-set | Avoid redundant classification after `llm_route` already decided. |
| `conversation` / `out_of_scope` exit graph immediately | No cache check, no decompose, no gather for pure chat or declined turns. |
| Fast path bypasses `decompose_node` | Single-ticker leaf-intent queries (price_action, technical_analysis, news_sentiment, rag_qa, valuation, screening, breakout_scan) skip decompose entirely — saves 1 LLM call + avoids 3 unnecessary data fetches. `macro_sector`, `investment_case`, `market_brief` always decompose (multi-component or no ticker). |
| `valuation` split from `rag_qa` | LLM routes metric/peer queries (P/E, P/B, ROE, EPS, EV/EBITDA) to `valuation` → `fundamentals.gather_data` (vnstock/KBS peer table). Report-content queries (revenue, profit, balance sheet) stay `rag_qa` → `retrieve_only` (RAG/SQL). Removes the `_is_sector_comparison` keyword heuristic — routing decision lives in the LLM, not keywords. |
| Re-plan loop capped at 1 | Re-decomposing identical input is usually deterministic; a second decompose rarely adds data. Cap 1 avoids infinite loop while still catching transient tool errors. |
| Self-critique capped at 1 retry | Critiquing the report costs 1 LLM call/turn; 1 retry catches most citation/truncation failures without doubling latency. Best-effort report returned on final fail. |
| Per-turn budget guard | `MAX_TURN_LLM_CALLS` + `MAX_TURN_SECONDS` bound total LLM calls and wall-clock so a pathological turn cannot loop unbounded. |
| Fan-out parallelised with timeout | Multi-ticker fan-out runs in a thread pool with a per-fetch timeout, so one hung source no longer serialises/stalls the whole turn. |
