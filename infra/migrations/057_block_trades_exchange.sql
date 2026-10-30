-- 057_block_trades_exchange.sql — exchange of each session (VNDirect stock_prices `floor`: HOSE / HNX / UPCOM).
-- A stock that moved (DSC UPCoM → HOSE 24/10/2024, NHH, TDP… before 2021) traded under the price band of the
-- exchange it was on that day; vn-portfolio's rule engine needs it for ceiling / floor rules and data checks.
-- Filled by block_trades_ingest (same VNDirect sessions); the bootstrap sensor backfills rows still NULL.

ALTER TABLE block_trades ADD COLUMN IF NOT EXISTS exchange TEXT;
