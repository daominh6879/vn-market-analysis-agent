-- Bài 27: agent session state + LangGraph checkpoint tables

-- Human-approval session tracking
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id  TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    state       JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'completed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    audit_log   JSONB NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions (status);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_expires ON agent_sessions (expires_at);

-- LangGraph checkpoint storage (main checkpoint metadata)
CREATE TABLE IF NOT EXISTS lg_checkpoints (
    thread_id           TEXT NOT NULL,
    checkpoint_ns       TEXT NOT NULL DEFAULT '',
    checkpoint_id       TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    checkpoint_data     BYTEA NOT NULL,
    metadata_data       BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- LangGraph channel value blobs
CREATE TABLE IF NOT EXISTS lg_checkpoint_blobs (
    thread_id       TEXT NOT NULL,
    checkpoint_ns   TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL,
    version         TEXT NOT NULL,
    blob_type       TEXT NOT NULL,
    blob            BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- LangGraph pending writes (in-flight step results)
CREATE TABLE IF NOT EXISTS lg_checkpoint_writes (
    thread_id       TEXT NOT NULL,
    checkpoint_ns   TEXT NOT NULL DEFAULT '',
    checkpoint_id   TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    idx             INTEGER NOT NULL,
    channel         TEXT NOT NULL,
    blob_type       TEXT NOT NULL,
    blob            BYTEA,
    task_path       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
