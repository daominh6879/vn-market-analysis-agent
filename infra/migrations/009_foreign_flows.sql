-- Foreign investor net trading flows (bài 23+).
-- Per-ticker daily: buy/sell/net in tỷ đồng.

CREATE TABLE IF NOT EXISTS foreign_flows (
    ticker          TEXT        NOT NULL,
    date            DATE        NOT NULL,
    buy_value       NUMERIC     NOT NULL DEFAULT 0,  -- tỷ đồng
    sell_value      NUMERIC     NOT NULL DEFAULT 0,  -- tỷ đồng
    net_value       NUMERIC     NOT NULL DEFAULT 0,  -- positive = net buy
    buy_volume      BIGINT      NOT NULL DEFAULT 0,
    sell_volume     BIGINT      NOT NULL DEFAULT 0,
    net_volume      BIGINT      NOT NULL DEFAULT 0,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_fflow_date    ON foreign_flows (date DESC);
CREATE INDEX IF NOT EXISTS idx_fflow_net     ON foreign_flows (date DESC, net_value);
