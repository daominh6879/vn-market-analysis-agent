-- Unified quotes table: world indices, commodities, crypto, FX, VN gold (bài 23+).
-- asset_class: 'equity_index' | 'commodity' | 'crypto' | 'fx' | 'gold_vn'
-- value / change_pct always in the symbol's native unit (see `unit` column).

CREATE TABLE IF NOT EXISTS market_quotes (
    symbol          TEXT        NOT NULL,
    asset_class     TEXT        NOT NULL,
    date            DATE        NOT NULL,
    value           NUMERIC     NOT NULL,
    change_abs      NUMERIC     NOT NULL DEFAULT 0,
    change_pct      NUMERIC     NOT NULL DEFAULT 0,
    extra           JSONB       NOT NULL DEFAULT '{}'::jsonb, -- mcap, bid/ask, etc.
    unit            TEXT        NOT NULL DEFAULT '',
    source          TEXT        NOT NULL DEFAULT '',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_mq_class_date ON market_quotes (asset_class, date DESC);
CREATE INDEX IF NOT EXISTS idx_mq_date       ON market_quotes (date DESC);
