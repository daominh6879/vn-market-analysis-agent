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
    IN([state: intent+ticker pre-set]) --> classify_node

    classify_node["classify_node\n• intent pre-set → pass through (no LLM)\n• intent absent → llm_classify()"]

    classify_node --> check_cache_node

    check_cache_node["check_cache_node\nRedis (exact) + Qdrant (vector)\nkey = intent+ticker [+normalized_question for RAG intents]"]
    check_cache_node -->|hit| END2([END — _cache_hit=True, report=cached])
    check_cache_node -->|miss| clarify_node

    clarify_node["clarify_node\n① detect_ambiguity — ticker/intent missing?\n② yes → interrupt() — wait for user answer\n③ merge answer → re-classify\n④ no ambiguity → pass through"]
    clarify_node --> decompose_node

    decompose_node["decompose_node\nLLM decomposes query\ninto N structured sub-queries"]
    decompose_node --> run_subqueries_node

    run_subqueries_node["run_subqueries_node\nFor each sub-query:\n  1. classify_hybrid → intent+ticker\n  2. gather_data() — NO LLM\nCollect sub_results list"]
    run_subqueries_node -->|human_approval=False| synthesize_final
    run_subqueries_node -->|human_approval=True| request_approval

    request_approval["request_approval\ninterrupt() — human reviews\n{ticker, risk, signals, news}"]
    request_approval -->|approved| synthesize_final
    request_approval -->|rejected| END3([END — error: rejected_by_user])

    synthesize_final["synthesize_final\nSingle LLM call over all sub_results\nMarkdown report (STRICT_NEUTRAL aware)"]
    synthesize_final --> cache_save_node

    cache_save_node["cache_save_node\nPersist report to cache"]
    cache_save_node --> END4([END — report in state])
```

## RAG intents (cache key includes normalized_question)

`_RAG_INTENTS = {"rag_qa", "screening", "macro_sector"}`

Different sector queries (banking vs construction) get distinct cache keys because
`normalized_question` is appended to the key — prevents cross-sector contamination.

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
| `breakout_scan` | `intents/breakout.gather_data` |
| `market_brief` | static placeholder |

## LLM call count per turn

| Path | LLM calls |
|---|---|
| Direct reply (social) | 1 (`llm_route`) |
| Agent path | 1 (`llm_route`) + 1 (clarify detect) + 1 (decompose) + N (classify per sub-query) + 1 (synthesize) = **4+N** |

## Key architecture decisions

| Decision | Reason |
|---|---|
| Two tools (`needs_agent_run` + `direct_reply`) | No free-form escape hatch — LLM makes explicit structured choice. Prevents LLM from answering financial queries from stale history. |
| `tool_choice="auto"` not `"required"` | `required` forces tool call for pure social turns → LLM calls `needs_agent_run` with reconstructed context → wrong. `auto` lets LLM reply freely for social, which we treat as `type=text`. |
| No-tool-call fallback → `type=text` | LLM chose not to call a tool → it's a social/conversational turn. Return its free text. |
| Exception fallback → `type=agent, market_brief` | Safe default: user gets some response. Better than empty. |
| Assistant history truncated to 120 chars in `llm_route` | Full reports in history let LLM answer new ticker queries from stale data. 120 chars = header only (confirms topic, not data). |
| `macro_sector` in `_RAG_INTENTS` | Sector queries need `normalized_question` in key to prevent banking/construction cache cross-contamination. |
| `classify_node` skips LLM when intent pre-set | Avoid redundant classification after `llm_route` already decided. |
