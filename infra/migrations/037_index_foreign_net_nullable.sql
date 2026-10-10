-- matched_value is unavailable for HNX/UPCOM (vnstock gives OHLC+volume only) → nullable too.
-- foreign_net is only computable for indices whose constituents we hold (VNINDEX, VN30, HNX30).
-- HNX / UPCOM have no foreign data, so store NULL instead of a misleading 0.
ALTER TABLE market_index_daily ALTER COLUMN foreign_net DROP NOT NULL;
ALTER TABLE market_index_daily ALTER COLUMN foreign_net DROP DEFAULT;
UPDATE market_index_daily SET foreign_net = NULL WHERE index_code IN ('HNX', 'UPCOM');
ALTER TABLE market_index_daily ALTER COLUMN matched_value DROP NOT NULL;
