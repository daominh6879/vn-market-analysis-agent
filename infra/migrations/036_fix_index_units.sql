-- market_index_daily.matched_value is raw VND. Rows up to 2025-08-11 were stored 1e6x too large
-- (VNINDEX 2025-03-21: 1.4989e19 instead of 1.4989e13). Idempotent: only touches values > 1e16.
UPDATE market_index_daily SET matched_value = matched_value / 1000000 WHERE matched_value > 1e16;

-- foreign_net (tỷ đồng) was always 0. foreign_flows is HOSE-only → backfill VNINDEX only.
UPDATE market_index_daily m SET foreign_net = f.net
  FROM (SELECT date, SUM(net_value) AS net FROM foreign_flows GROUP BY date) f
 WHERE m.index_code = 'VNINDEX' AND m.date = f.date;
