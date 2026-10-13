# intent_model — trained router model (prototype, not wired into the agent)

Goal: serve confident queries without an LLM call (latency/cost); low confidence falls back to `llm_route`.
Target = the fields the live router produces: intent, route_type, time_context, topics, risk_flags, language,
ticker/sector, screening filters. v1 is single-turn. Results, decisions and caveats: `docs/notes/intent-model.md`.

| Field | How |
|---|---|
| intent, route_type, language, topics, risk_flags | bge-m3 embedding + logistic regression (`train.py`) or a fine-tuned encoder (`finetune.py`) |
| time_context | model predicts kind (none/relative/absolute) + unit; numbers/year/quarter read by regex; `resolve_time` makes dates with the router's convention |
| ticker / sector | NOT modelled — reuse `agents.focus.extract_focus_entities/sector` (deterministic, universe + alias based) |
| screening filters | NOT modelled in v1 (needs token tagging); cascade falls back to the LLM for `screening` |

Run from repo root (`.venv\Scripts\activate` first):

1. `python intent_model/build_seed.py`          -> data/seed.jsonl (evals/*.json + data/seed_manual*.jsonl; review by hand)
2. `python intent_model/generate_synthetic.py`  -> data/synthetic.jsonl (annotated, via LLM factory;
   `--append --per-intent 300 --workers 6` adds rows, `--intents a b` restricts)
3. `python intent_model/fix_tickers.py`         -> tickers := extract_focus_entities (LLM guess kept as gen_tickers). Run AFTER step 2 finishes (it rewrites the whole file).
4. `python intent_model/train.py`               -> frozen-embedding baseline: artifacts/heads.joblib + per-field report + threshold table
5. `python intent_model/finetune.py [--model intfloat/multilingual-e5-small --epochs 6]` -> artifacts/ft/<model>/ + same report

Both trainers read data through `common.load_synthetic`, which drops rows whose label contradicts the router's
ROUTING_RULES (market-wide intents naming a stock, "no time" with an explicit time range, ...) and any query that
is also in the gold seed set. `data/synthetic.jsonl` itself is never filtered on disk.

Nothing here is imported by the live agent.
