-- Dividend history (cash + stock) with ex-date, for dividend yield questions.
-- One row per (ticker, ex_date, kind) — re-fetching is idempotent.
CREATE TABLE IF NOT EXISTS dividends (
    id               SERIAL      PRIMARY KEY,
    ticker           TEXT        NOT NULL,
    kind             TEXT        NOT NULL,        -- 'cash' | 'stock'
    ex_date          DATE        NOT NULL,        -- ngày GDKHQ
    record_date      DATE,
    payment_date     DATE,
    cash_per_share   NUMERIC,                     -- VND/cp (kind='cash')
    stock_ratio_pct  NUMERIC,                     -- % (kind='stock'; 20 = 20% = 100:20)
    note             TEXT        NOT NULL DEFAULT '',
    source           TEXT        NOT NULL DEFAULT '',
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, ex_date, kind)
);
CREATE INDEX IF NOT EXISTS idx_dividends_ticker ON dividends (ticker, ex_date DESC);

-- Daily snapshot of series that providers only expose "as of now" (SJC gold, SBV central rate),
-- so "so với hôm qua" can be answered from our own history.
CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date DATE        NOT NULL,
    series        TEXT        NOT NULL,           -- 'sjc_gold' | 'sbv_central_rate' | 'vcb_usd'
    buy           NUMERIC,
    sell          NUMERIC,
    value         NUMERIC,                        -- single-value series (central rate)
    unit          TEXT        NOT NULL DEFAULT '',
    source        TEXT        NOT NULL DEFAULT '',
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, series)
);
