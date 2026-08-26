-- Master securities table: HOSE/HNX/UPCOM universe (bài 23+).
-- Used for breadth expansion and sector performance.
-- Refresh periodically via ingest/fetch_universe.py.

CREATE TABLE IF NOT EXISTS securities (
    ticker          TEXT        PRIMARY KEY,
    exchange        TEXT        NOT NULL,   -- 'HOSE', 'HNX', 'UPCOM'
    sector          TEXT        NOT NULL DEFAULT 'Unknown',
    industry        TEXT        NOT NULL DEFAULT 'Unknown',
    company_name    TEXT        NOT NULL DEFAULT '',
    listed_shares   BIGINT      NOT NULL DEFAULT 0,
    free_float      NUMERIC     NOT NULL DEFAULT 1.0, -- 0..1
    index_member    TEXT[]      NOT NULL DEFAULT '{}', -- ['VN30', 'VN100']
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sec_exchange  ON securities (exchange);
CREATE INDEX IF NOT EXISTS idx_sec_sector    ON securities (sector);
CREATE INDEX IF NOT EXISTS idx_sec_index     ON securities USING GIN (index_member);
