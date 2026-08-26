-- OHLCV daily candles for VN30 constituents + indices (bài 23).
-- Primary source: VciDirectProvider (not vnstock).
-- Replaces live API calls in tools: query this table for market_performance + market_breadth.
-- Upsert-safe: re-running pipeline is idempotent.

CREATE TABLE IF NOT EXISTS ohlcv_daily (
    ticker      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    open        NUMERIC NOT NULL,
    high        NUMERIC NOT NULL,
    low         NUMERIC NOT NULL,
    close       NUMERIC NOT NULL,
    volume      BIGINT  NOT NULL DEFAULT 0,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON ohlcv_daily (date DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv_daily (ticker, date DESC);
