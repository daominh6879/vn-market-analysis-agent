-- Track indexed documents; never delete rows, mark status='deleted' for audit

CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'deleted'
    source_uri   TEXT NOT NULL,
    collection   TEXT NOT NULL DEFAULT 'hpg_structural',
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at   TIMESTAMPTZ
);
