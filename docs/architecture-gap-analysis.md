# Architecture Gap Analysis — stock-ai-chat-agent-architecture.md vs current code

Review of `docs/stock-ai-chat-agent-architecture.md` against the live agent pipeline
(`agents/`, `memory/turn_handler.py`). Date: 2026-09-05.

## Verdict

~85% of the document is already implemented. The doc largely restates the mature
design the project converged on (LLM-on-top router + deterministic carry-forward + sub-query
decomposition). Two gaps were closed (mixed-intent decomposition, typed long-term prefs);
LLM Router and microservices remain deferred.

## Mapping — doc section → current code

| Doc concept | Section | Current code | Status |
|---|---|---|---|
| Conversation Router (GENERAL/STOCK/OUT_OF_SCOPE) | 3, 7 | `agents/conversation_router.py` `llm_route` — `needs_agent_run` / `direct_reply` / `out_of_scope` | ✅ done |
| Intent taxonomy | 4 | `agents/classifier.py` `INTENTS` (12 intents) | ✅ done |
| Ticker resolver (deterministic, not LLM) | 5 | `conversation_router._validate_ticker` + `core.tickers.get_tickers` | ✅ done |
| Context resolver / short-term memory | 6.1 | `agents/focus.py` `resolve_focus` (FRESH/CONTINUE/AMBIGUOUS) | ✅ done |
| Topic switching (no forced mode) | 8 | `focus.py` carry-forward — a new ticker is FRESH, never inherited | ✅ done |
| Intent vs execution plan | 9 | `graph.py` `decompose_node` → `sub_tasks` → `run_subqueries_node` | ✅ done |
| Stock agent + tool layer | 11 | `agents/intents/*` (price_action, technical, fundamentals, valuation, …) | ✅ done |
| Context builder (no full-history dump) | 12 | `graph.py` `synthesize_final` builds focused context | ✅ done |
| Data storage split | 14 | Postgres (turns/prefs), Qdrant (episodic/RAG), Redis (cache), MinIO (files) | ✅ done |
| Out-of-scope safety boundary | 14.x | `node_out_of_scope` + `OUT_OF_SCOPE_REPLY` + `out_of_scope` tool | ✅ done |
| Boundary decision model (support_status) | 14.6 | `RouteResult.type` + intent out_of_scope (implicit, not a full schema) | 🟡 partial |
| Long-term user memory (typed) | 6.2 | `extract_preferences` typed keys + `memory/reader.py` `load_typed_preferences` | ✅ done |
| LLM Router (model selection) | 10 | absent — single `create_client()` | ⏸ defer |
| Mixed-intent message decomposition | 7 Case C, 14.3 | `decompose` tool + `RouteResult(type="mixed")` + turn_handler `_mixed_parts` | ✅ done |
| Microservices architecture | 13 | architectural vision, not a code task | ⏸ defer |

## Gaps

### 1. Mixed-intent message decomposition — ✅ resolved (2026-09-05)

Doc §7 Case C and §14.3: one message can carry multiple tasks.

```
"How was your day? By the way, should I buy FPT?"
        → [general_conversation, investment_decision]
```

Implemented via a 4th router tool `decompose` (`agents/conversation_router.py`) → new
`RouteResult(type="mixed", segments=[...])`, executed by `_mixed_parts` in
`memory/turn_handler.py`. A mixed message now runs the stock segment through the graph
and appends the drafted general/out-of-scope replies in one response. Supported +
out-of-scope mixes answer the supported part and decline the unsupported part (§14.3).

### 2. Typed long-term preferences — ✅ resolved (2026-09-05)

Doc §6.2 shows a typed schema (`favorite_tickers`, `preferred_market`,
`preferred_analysis`). Added typed keys to `memory/extractor.py` `extract_preferences`
(list value preserved) and `memory/reader.py` `load_typed_preferences` / `_parse_typed`,
rendered as a structured profile block in `_build_system`. Machine-readable, not just
prompt text.

### 3. LLM Router / model selection (defer)

Doc §10 routes by task complexity → cheap vs strong model. Conflicts with the project
rule `LLM_PROVIDER=deepseek` (single provider, factory `create_client()`), and would be
speculative until multi-model infra exists. Defer.

### 4. Microservices (defer)

Doc §13 splits into Conversation/Context/Intent/Memory services. Architectural vision;
the current modular packages (`agents/`, `memory/`, `tools/`) already give the same seams
inside one process. Defer until there is an operational reason to split.

## Recommendation

Gaps #1 (mixed-intent) and #2 (typed prefs) closed. Remaining: LLM Router and
microservices — deferred, both would be speculative and one conflicts with the
single-provider rule.
