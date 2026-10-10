-- ohlcv_daily stores stock prices in nghìn đồng (VCB 56.7 = 56,700 VND).
-- Rows ingested before 2025-09-09 via the VCI fallback were stored in VND instead
-- (e.g. AAM 6981.52 on 2025-09-08 vs 7.25 on 2025-09-09), mixing units inside one ticker.
-- Index rows (VNINDEX...) are in points and are left untouched.
-- Idempotent: only rows with close >= 1000 are touched, and no stock trades at 1,000,000 VND.

CREATE TABLE IF NOT EXISTS ohlcv_daily_unitfix_backup AS
  SELECT * FROM ohlcv_daily
  WHERE close >= 1000 AND ticker NOT IN ('VNINDEX','VN30','HNX','HNX30','UPCOM');

UPDATE ohlcv_daily
   SET open = open / 1000, high = high / 1000, low = low / 1000, close = close / 1000
 WHERE close >= 1000 AND ticker NOT IN ('VNINDEX','VN30','HNX','HNX30','UPCOM');
