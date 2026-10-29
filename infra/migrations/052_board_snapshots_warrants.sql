-- 052_board_snapshots_warrants.sql — collected from today on (no history exists): the closing price board of HOSE
-- stocks (best bid / offer, for khung 7.6-A "dư mua trần" at a ceiling close) and the covered warrants on HOSE
-- (7.6-D cw_expiry_window: last trading day per underlying). Source: SSI iBoard through vnmarket.

CREATE TABLE IF NOT EXISTS board_close_snapshot (
    ticker          TEXT        NOT NULL,
    date            DATE        NOT NULL,
    ref             NUMERIC,
    ceiling         NUMERIC,
    floor           NUMERIC,
    close           NUMERIC,
    matched_volume  BIGINT,
    bid1            NUMERIC,
    bid1_volume     BIGINT,
    offer1          NUMERIC,
    offer1_volume   BIGINT,
    admin_status    TEXT        NOT NULL DEFAULT '',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS covered_warrants (
    symbol              TEXT        PRIMARY KEY,
    underlying          TEXT        NOT NULL,
    issuer              TEXT        NOT NULL DEFAULT '',
    cw_type             TEXT        NOT NULL DEFAULT '',
    exercise_price      NUMERIC,
    exercise_ratio      TEXT        NOT NULL DEFAULT '',
    first_trading_date  DATE,
    last_trading_date   DATE,
    maturity_date       DATE,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cw_underlying ON covered_warrants (underlying, last_trading_date);
