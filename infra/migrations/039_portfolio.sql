-- Per-user cash ledger + buy/sell transactions. Positions are derived from this table
-- (no separate holdings table), so there is a single source of truth.
-- price is in VND per share (ohlcv_daily stores nghìn đồng — convert when valuing).
-- amount is the signed cash effect: deposit/sell/dividend > 0, withdraw/buy/fee < 0.
CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id          SERIAL      PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL DEFAULT 'default',
    txn_date    DATE        NOT NULL,
    kind        TEXT        NOT NULL CHECK (kind IN ('deposit','withdraw','buy','sell','dividend','fee')),
    ticker      TEXT,
    quantity    BIGINT,
    price       NUMERIC,
    fee         NUMERIC     NOT NULL DEFAULT 0,
    tax         NUMERIC     NOT NULL DEFAULT 0,
    amount      NUMERIC     NOT NULL,
    note        TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_txn_user_date ON portfolio_transactions (user_id, txn_date);
