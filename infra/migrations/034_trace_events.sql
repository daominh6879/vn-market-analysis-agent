-- Observability: trace events + LLM cost ledger (replaces traces/*.jsonl)

CREATE TABLE IF NOT EXISTS trace_events (
    id         BIGSERIAL PRIMARY KEY,
    ts         DOUBLE PRECISION NOT NULL,      -- epoch seconds
    kind       TEXT NOT NULL,                  -- turn_start | turn_end | llm | tool | gate | node_start | node_end
    request_id TEXT NOT NULL DEFAULT '',
    payload    JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_trace_events_ts  ON trace_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_trace_events_rid ON trace_events (request_id);

CREATE TABLE IF NOT EXISTS llm_usage (
    id            BIGSERIAL PRIMARY KEY,
    ts            DOUBLE PRECISION NOT NULL,
    request_id    TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage (ts DESC);
