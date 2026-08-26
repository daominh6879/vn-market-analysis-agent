-- Market index daily candles (bài 23+).
-- Source: SSI iBoard (real VNINDEX/HNX/UPCOM).
-- Replaces VN30 proxy for index tools.
-- matched_value: total trading value on exchange (tỷ đồng).

CREATE TABLE IF NOT EXISTS market_index_daily (
    index_code      TEXT        NOT NULL,   -- 'VNINDEX', 'HNX', 'UPCOM', 'VN30', 'HNX30'
    date            DATE        NOT NULL,
    open            NUMERIC     NOT NULL,
    high            NUMERIC     NOT NULL,
    low             NUMERIC     NOT NULL,
    close           NUMERIC     NOT NULL,
    change_pts      NUMERIC     NOT NULL DEFAULT 0,
    change_pct      NUMERIC     NOT NULL DEFAULT 0,
    matched_volume  BIGINT      NOT NULL DEFAULT 0,
    matched_value   NUMERIC     NOT NULL DEFAULT 0, -- tỷ đồng
    foreign_net     NUMERIC     NOT NULL DEFAULT 0, -- tỷ đồng, positive = net buy
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_code, date)
);

CREATE INDEX IF NOT EXISTS idx_midx_date       ON market_index_daily (date DESC);
CREATE INDEX IF NOT EXISTS idx_midx_code_date  ON market_index_daily (index_code, date DESC);
