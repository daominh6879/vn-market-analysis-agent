-- Proprietary (tự doanh) trading per ticker/day, same units as foreign_flows (values in tỷ đồng).
-- Written by Dagster asset proprietary_flows_ingest. History starts 2022-05-17 (HOSE resumed publishing that day).
CREATE TABLE IF NOT EXISTS proprietary_flows (
    ticker       TEXT        NOT NULL,
    date         DATE        NOT NULL,
    buy_value    NUMERIC,
    sell_value   NUMERIC,
    net_value    NUMERIC,
    buy_volume   BIGINT,
    sell_volume  BIGINT,
    net_volume   BIGINT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_proprietary_flows_date ON proprietary_flows (date DESC);

-- Forward-test log of breakout events (written daily by Dagster asset breakout_signals_daily).
-- prop_gate = proprietary net buy over the last 5 sessions > 1% of traded value (the gate that looked useful in backtests).
-- Both gated and ungated events are logged so the realized outcome of the gate can be compared (signals/breakout_monitor.py).
CREATE TABLE IF NOT EXISTS breakout_signals (
    ticker         TEXT        NOT NULL,
    date           DATE        NOT NULL,
    signal_type    TEXT        NOT NULL CHECK (signal_type IN ('SHORT','MID','LONG')),
    close          NUMERIC     NOT NULL,
    avg_value_bn   NUMERIC     NOT NULL,
    prop_ratio_5d  NUMERIC,
    prop_ratio_20d NUMERIC,
    prop_gate      BOOLEAN     NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date, signal_type)
);
CREATE INDEX IF NOT EXISTS idx_breakout_signals_date ON breakout_signals (date DESC, signal_type, prop_gate);
