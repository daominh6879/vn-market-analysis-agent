# Router/memory redesign — align with mature stock-chat agent architecture

## Context

User reports frequent wrong-intent routing. Root-cause review of `agents/conversation_router.py`
+ deps (`agents/classifier.py`, `agents/graph.py`, `memory/turn_handler.py`,
`memory/clarification.py`) found the wrong-intent symptoms trace back to one structural
gap, not isolated bugs: **there is no explicit dialogue-state / focus object**. Ticker,
sector, intent are re-derived independently in 3+ places (router, `clarify_node`,
`build_single_subtask_node`), each with its own heuristic, so they drift out of sync.

Mature stock-chat / financial-assistant agents avoid this by splitting the turn into
distinct layers, each with one job:

1. **Entity/slot extraction** — deterministic (ticker gazetteer/regex match) first;
   LLM only as fallback for company-name → ticker resolution. Never a string-heuristic
   guess for "did the user name something new."
2. **Dialogue State Tracker (DST)** — one explicit `focus` object per conversation:
   `{tickers: [...], sector, intent, metric, updated_at}`. Updated by a fixed rule:
   new entity found in the message → replace focus; no new entity + a continuation
   phrase → carry the *whole* prior focus forward unchanged (not just one ticker).
3. **Intent classification** — a single LLM call that receives the DST's already-resolved
   slots as ground truth. It is not asked to also infer continuation/carry-forward itself.
4. **Task routing** — maps (intent, slots) → skill/tool pipeline. Our `agents/graph.py`
   decompose → gather → synthesize → critique pipeline already matches this well; not in
   scope for this change.
5. **Long-term memory** — user preferences / episodes, already separated from dialogue
   state in `memory/reader.py` / `memory/episodic.py`; not in scope for this change.

Our app collapses layers 1-3 into one LLM tool call (`needs_agent_run`) that must
simultaneously classify intent, extract/resolve tickers, decide continuation-vs-fresh,
and parse time — a "god tool." Combined with `_read_prior` reading a single raw
`state["ticker"]` string as the only cross-turn memory, this produces exactly the
symptoms reported: a new ticker mentioned alongside a continuation phrase gets ignored
(inherits stale subject), and multi-ticker comparisons collapse to one ticker on the
next turn.

**Goal of this change:** introduce an explicit focus/slot object as the single source of
truth for "what is this conversation currently about," computed deterministically where
possible, and have every consumer (router, clarify, fast-path builder) read from it
instead of re-deriving it independently.

## Target design

### New: `agents/focus.py`

```python
@dataclass
class Focus:
    tickers: list[str]       # all tickers currently in scope (comparison-safe)
    sector: str              # sector/index subject when no specific ticker (macro_sector/market_brief)
    intent: str
    query: str                # last resolved self-contained query (for trace/debug only)
    updated_at: str           # ISO timestamp

def extract_focus_entities(query: str) -> list[str]:
    """Deterministic entity extraction — wraps core.tickers.extract_tickers.
    This is the ONLY place that decides "did the user name something new."
    """

def resolve_focus(query: str, prior: Focus | None) -> tuple[Focus_update_kind, list[str]]:
    """Pure, no LLM. Returns whether this turn introduces new entities or is a bare
    continuation, and the entity list to use (new ones, or prior.tickers carried forward).
    """
```

Replaces `_is_bare_continuation` (`agents/conversation_router.py:181-189`) with a
deterministic check: continuation == `extract_focus_entities(query)` is empty AND a
continuation phrase matches. A message with a new ticker can never be misread as
"inherit old subject," because the entity list is checked directly instead of guessed
from marker phrases alone.

### Changed: `agents/conversation_router.py`

- `_inject_last_context` takes a `Focus` (full ticker list) instead of `last_intent: str,
  last_subject: str` (single ticker). Injected note lists ALL prior tickers, so a
  continuation after a comparison keeps both.
- `_is_bare_continuation(query)` → `resolve_focus(query, prior_focus)`; drop the
  marker-substring-only path.
- `_fallback_classify` (used when the router LLM skips tools or misuses `direct_reply`)
  gets the same `Focus` passed in, and applies the identical carry-forward rule before
  calling `classify_hybrid` — today this path has zero continuation memory
  (`agents/conversation_router.py:276-299`), so it silently drops intent on any bare
  continuation that reaches it.
- `_validate_ticker`: on drop, keep the raw hallucinated ticker in `reason` for
  traceability (cheap, no behavior change) — `agents/conversation_router.py:227-250`.

### Changed: `memory/turn_handler.py`

- `_read_prior` (`memory/turn_handler.py:63-89`) returns a `Focus` (via
  `agents/focus.py`) built from checkpoint `state["ticker"]` **plus**
  `extract_focus_entities(state.get("original_query", ""))` — so a comparison turn's
  2nd/3rd ticker survives into the next turn's focus even though the graph's top-level
  `state["ticker"]` only ever stored one.
- `run_turn` / `stream_turn` pass `focus=` into `llm_route` instead of
  `last_intent=`/`last_subject=`.

### Changed: `agents/graph.py`

- `build_single_subtask_node` (`agents/graph.py:783-812`) already re-extracts tickers
  from `original_query` as a patch for the same underlying gap — once `Focus` carries
  the full ticker list end-to-end from the router, this becomes a straight read of
  `state["tickers"]` (plural) instead of a second independent re-extraction. Add
  `tickers: list[str]` to `AgentState` alongside the existing singular `ticker` (keep
  `ticker` = `tickers[0]` for backward compat with code that only reads `ticker`).

### Not changed (confirm before touching)

- `memory/clarification.py` Case 0 (`_is_bare_ticker`) — re-asks intent for any bare
  ticker regardless of what the router already decided. Possibly intentional UX
  (confirm with user before changing); this pass only fixes the focus-tracking gap that
  causes wrong-intent inheritance, not the clarify-node double-check behavior.

## Migration steps

1. Add `agents/focus.py` (`Focus` dataclass, `extract_focus_entities`, `resolve_focus`)
   — pure functions, unit-testable in isolation, no LLM calls.
2. Add `tickers: list[str]` field to `AgentState` (`agents/state.py`), populated
   alongside `ticker` everywhere it's currently set.
3. Update `conversation_router.llm_route` signature: replace
   `last_intent: str = "", last_subject: str = ""` with `focus: Focus | None = None`.
   Update `_inject_last_context` and `_fallback_classify` accordingly.
4. Update `memory/turn_handler._read_prior` to build and return a `Focus`.
5. Update both call sites (`run_turn`, `stream_turn`) to pass `focus=`.
6. Update `build_single_subtask_node` to read `state["tickers"]` when present, falling
   back to `extract_tickers(query)` only for direct-graph-invocation callers (tests) that
   never went through the router.
7. Keep `RouteResult["ticker"]` as the single first ticker for backward compat with
   existing intent gather signatures (`gather_data(ticker, query, tc)`); add
   `RouteResult["tickers"]` alongside it for the fan-out/comparison path.

## Verification

- New `tests/test_focus.py`: pure unit tests for `resolve_focus` —
  - new ticker present → fresh, ticker list = new tickers, no carry-forward
  - no ticker + continuation phrase → carry forward prior tickers unchanged
  - no ticker + no continuation phrase + no other signal → ambiguous (let LLM/clarify decide)
  - continuation phrase AND new ticker in the same message → fresh (this is the bug
    being fixed — must NOT carry forward)
- Extend `tests/test_router_intents_cache.py`:
  - prior focus tickers=[VCB], intent=valuation; query "phân tích thêm HPG" → fresh
    intent for HPG, VCB dropped, not inherited as valuation.
  - prior focus tickers=[BID, CTG] (from a comparison turn); query "phân tích sâu hơn"
    → both tickers carried forward, not just BID.
  - router bypasses tools (direct_reply misuse) on a bare continuation with focus set →
    `_fallback_classify` still inherits, doesn't drop to conversation.
- Run full `tests/test_router_intents_cache.py`, `tests/test_phase2.py`,
  `tests/test_fireant_ingest.py` (unrelated but touches shared fixtures) to confirm no
  regression.
- Manual: run 3-turn conversation through `run_turn` (or the API) —
  1. "so sánh BID và CTG" (comparison, valuation)
  2. "phân tích sâu hơn" (continuation — expect both tickers kept)
  3. "còn VCB thì sao" (new subject — expect fresh classification, not inherited valuation
     unless VCB naturally classifies that way)
  Inspect trace `reason` field on each turn to confirm decisions match expectation.
