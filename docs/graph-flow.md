# Agent Graph Flow

## LLM-on-top turn entry (stream_turn)

```mermaid
flowchart TD
    UM([User Message]) --> IT{graph\ninterrupted?}

    IT -->|yes — awaiting clarification| RESUME["Command(resume=user_message)\n→ graph continues from clarify_node"]
    RESUME --> GOUT

    IT -->|no| LLM["llm_route(query, history, system_prompt)\nSingle LLM call with 2 tools:\n• needs_agent_run(intent, ticker, query)\n• direct_reply(text)"]

    LLM -->|needs_agent_run tool called| PRE["Build state\nintent + ticker pre-set\nclassify_node skips LLM"]
    LLM -->|direct_reply tool called| TEXT([Stream tool text to user\nno graph invoked])
    LLM -->|no tool call — free text| TEXT2([Stream LLM text to user\nno graph invoked])
    LLM -->|exception| FALLBACK([type=agent\nintent=market_brief\nfail-safe])

    PRE --> GRAPH["Graph.invoke(state)"]
    FALLBACK --> GRAPH
    GRAPH --> GOUT{graph\nresult}

    GOUT -->|_cache_hit| CACHED([Stream cached report])
    GOUT -->|clarify interrupt| CLARIFY([Stream clarification question\ngraph stays interrupted])
    GOUT -->|report| RPT([Stream report to user])
```

### Routing decision logic

| LLM action | Meaning | Result |
|---|---|---|
| calls `needs_agent_run` | Financial data needed | `type=agent` → graph |
| calls `direct_reply` | Pure social turn | `type=text` → stream direct |
| no tool call (free text) | LLM chose to answer directly | `type=text` → stream direct |
| exception | LLM failed | `type=agent, market_brief` → graph |

`direct_reply` description is narrow (pure social: greetings, ack, thanks, reactions).
LLM cannot justify calling it for any query referencing a stock, sector, or financial metric.

## Graph internals

```mermaid
flowchart TD
    IN([User query\nintent may be pre-set by llm_route]) --> classify_node

    classify_node["classify_node\n• intent pre-set → pass through (no LLM)\n• intent absent → llm_classify()"]

    classify_node -->|intent == conversation| END0([END — stream_turn handles streaming])
    classify_node -->|intent != conversation| check_cache_node

    check_cache_node["check_cache_node\nRedis only — single tier\nkey = (tenant_id, intent, ticker, scope, prompt_version, model_version)\nscope = question-hash for _ALWAYS_SCOPE_INTENTS"]
    check_cache_node -->|hit| END2([END — _cache_hit=True, report=cached])
    check_cache_node -->|miss| clarify_node

    clarify_node["clarify_node\n① detect_ambiguity — ticker/intent missing?\n② yes → interrupt() — wait for user answer\n③ merge answer → re-classify\n④ no ambiguity → pass through"]
    clarify_node --> route_check{simple\nquery?}

    route_check -->|"intent∈{price_action,technical,\nnews_sentiment,rag_qa,valuation,\nscreening,breakout}\nAND ticker set"| fast_node["build_single_subtask_node\nWrap intent+ticker → 1 sub_task\nno LLM call"]
    route_check -->|"market_brief / investment_case\n/ macro_sector\nOR no ticker"| decompose_node

    fast_node --> run_subqueries_node

    decompose_node["decompose_node\nLLM tool-call decomposition\n→ N sub_tasks [{intent, tickers, question}]\nintent + tickers pre-set in each task"]
    decompose_node --> run_subqueries_node

    run_subqueries_node["run_subqueries_node\nFor each sub_task (intent pre-classified):\n  gather_data(ticker, question) — NO LLM\n  fan-out per ticker for price/technical intents\nCollect sub_results list\nset sub_results_empty_ratio"]
    run_subqueries_node --> replan_check{">50% empty\nAND not replanned?"}

    replan_check -->|"yes — replan (≤1)\nfolds replan_note"| decompose_node
    replan_check -->|no| approval_check{human_approval?}

    approval_check -->|True| request_approval
    approval_check -->|False| synthesize_final

    request_approval["request_approval\ninterrupt() — human reviews\n{ticker, risk, signals, news}"]
    request_approval -->|approved| synthesize_final
    request_approval -->|rejected| END3([END — error: rejected_by_user])

    synthesize_final["synthesize_final\nSingle LLM call over all sub_results\nMarkdown report (STRICT_NEUTRAL aware)\nfolds critique_feedback on retry"]
    synthesize_final --> critique_report_node

    critique_report_node["critique_report_node\nLLM self-check vs checklist\n(citation, no truncation)\n→ {pass, feedback}"]
    critique_report_node --> critique_check{pass?}

    critique_check -->|"pass"| cache_save_node
    critique_check -->|"fail & attempts < MAX_CRITIQUE (1)"| synthesize_final
    critique_check -->|"fail & attempts ≥ MAX_CRITIQUE"| cache_save_node

    cache_save_node["cache_save_node\nPersist report to Redis"]
    cache_save_node --> END4([END — report in state])
```

## Cache design (single-tier Redis)

Key model: `(tenant_id, intent, ticker, scope, prompt_version, model_version)` — **no full question text**.

Same intent+ticker (+scope where applicable) always returns the same cached answer, cross-conversation.

`_ALWAYS_SCOPE_INTENTS` (`macro_sector`, `rag_qa`, `valuation`, `screening`, `breakout_scan`)
add an 8-char question-hash `scope` to the key — for these, ticker alone does not specify the
query (e.g. "P/E HPG" ≠ "ROE HPG"). All other intents use `scope=""` (ticker fully differentiates).

`original_query` is passed to `make_cache_key` only for stable multi-ticker extraction
(e.g. "HPG so với VCB" → `ticker="HPG|VCB"`), not stored in key.

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

`conversation` intent → never cached.

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
when a sub-task has multiple tickers, each ticker is fetched independently and results merged.

## LLM call count per turn

Base counts (critique passes on first try, no re-plan):

| Path | LLM calls |
|---|---|
| Direct reply (social) | 1 (`llm_route`) |
| Simple query (specific ticker, leaf intent) | 1 (`llm_route`) + 1 (`synthesize_final`) + 1 (`critique_report_node`) = **3** |
| Complex query (no clarify) | 1 (`llm_route`) + 1 (`decompose_node`) + 1 (`synthesize_final`) + 1 (`critique_report_node`) = **4** |
| Complex query (with clarify) | +1 (clarify re-classify) = **5** |

Bounded extra calls (each capped at 1):

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

`_is_empty_result` treats an empty string **and** error stubs (`lỗi:`, `Không có dữ liệu`, `ticker không được rỗng`) as unusable — so an error string no longer counts as "data present".

## Key architecture decisions

| Decision | Reason |
|---|---|
| Two tools (`needs_agent_run` + `direct_reply`) | No free-form escape hatch — LLM makes explicit structured choice. Prevents LLM from answering financial queries from stale history. |
| `tool_choice="auto"` not `"required"` | `required` forces tool call for pure social turns → LLM calls `needs_agent_run` with reconstructed context → wrong. `auto` lets LLM reply freely for social, which we treat as `type=text`. |
| No-tool-call fallback → `type=text` | LLM chose not to call a tool → it's a social/conversational turn. Return its free text. |
| Exception fallback → `type=agent, market_brief` | Safe default: user gets some response. Better than empty. |
| Assistant history truncated to 120 chars in `llm_route` | Full reports in history let LLM answer new ticker queries from stale data. 120 chars = header only (confirms topic, not data). |
| Intent-level cache key (no question text) | Same intent+ticker = same data shape = same answer. Avoids cache misses from paraphrase. `original_query` feeds ticker extraction only. |
| No per-sub-query `classify_hybrid` | `decompose_node` uses tool calling — LLM returns structured `{intent, tickers, question}` directly. Saves N LLM calls per turn. |
| `classify_node` skips LLM when intent pre-set | Avoid redundant classification after `llm_route` already decided. |
| `conversation` intent exits graph immediately | No cache check, no decompose, no gather for pure chat turns. |
| Fast path bypasses `decompose_node` | Single-ticker leaf-intent queries (price_action, technical_analysis, news_sentiment, rag_qa, valuation, screening, breakout_scan) skip decompose entirely — saves 1 LLM call + avoids 3 unnecessary data fetches. `macro_sector`, `investment_case`, `market_brief` always decompose (multi-component or no ticker). |
| `valuation` split from `rag_qa` | LLM routes metric/peer queries (P/E, P/B, ROE, EPS, EV/EBITDA) to `valuation` → `fundamentals.gather_data` (vnstock/KBS peer table). Report-content queries (revenue, profit, balance sheet) stay `rag_qa` → `retrieve_only` (RAG/SQL). Removes the `_is_sector_comparison` keyword heuristic — routing decision lives in the LLM, not keywords. |
| Re-plan loop capped at 1 | Re-decomposing identical input is usually deterministic; a second decompose rarely adds data. Cap 1 avoids infinite loop while still catching transient tool errors. |
| Self-critique capped at 1 | Critiquing the report costs 1 LLM call/turn; 1 retry catches most citation/truncation failures without doubling latency. Best-effort report returned on final fail. |
