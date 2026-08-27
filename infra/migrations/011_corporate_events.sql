-- Corporate events: GDKHQ, dividend, rights issue, AGM, etc. (Phase 5)
-- ex_date is the last date you own shares to qualify (ngày GDKHQ).
-- UNIQUE on (ticker, event_type, ex_date) — re-scraping is idempotent.

CREATE TABLE IF NOT EXISTS corporate_events (
    id          SERIAL      PRIMARY KEY,
    ticker      TEXT        NOT NULL,
    event_type  TEXT        NOT NULL,   -- 'gdkhq' | 'dividend' | 'rights_issue' | 'agm' | 'other'
    ex_date     DATE,                   -- ngày giao dịch không hưởng quyền
    record_date DATE,                   -- ngày chốt danh sách cổ đông
    ratio       NUMERIC,                -- tỷ lệ (ví dụ 10 = 10%, 100:10)
    note        TEXT        NOT NULL DEFAULT '',
    source_url  TEXT        NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, event_type, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_corpev_ticker  ON corporate_events (ticker);
CREATE INDEX IF NOT EXISTS idx_corpev_exdate  ON corporate_events (ex_date);
CREATE INDEX IF NOT EXISTS idx_corpev_type    ON corporate_events (event_type, ex_date);
