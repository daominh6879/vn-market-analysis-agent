-- 051_fireant_financial_data.sql — structured per-period values from FireAnt financial-data (quarterly from 2011):
-- bank loan groups 1–5, customer loans/deposits, loan-loss reserves (khung 5.6 / 5.8 instead of reading PDF notes),
-- broker loans and the sector-wide broker loan total (3.3 dư nợ margin toàn thị trường). Long format, one row per field.

CREATE TABLE IF NOT EXISTS fireant_financial_data (
    ticker      TEXT        NOT NULL,
    year        INTEGER     NOT NULL,
    quarter     INTEGER     NOT NULL,              -- 0 = full year
    field       TEXT        NOT NULL,              -- FireAnt name, e.g. WatchlistDebt, NPLToLoan, SectorLoan
    value       NUMERIC,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, year, quarter, field)
);

-- shareholders now holds VNDirect and FireAnt rows (FireAnt report dates are fresher)
ALTER TABLE shareholders ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'vnd';
ALTER TABLE shareholders DROP CONSTRAINT IF EXISTS shareholders_ticker_name_effective_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS shareholders_source_key ON shareholders (ticker, source, name, effective_date) NULLS NOT DISTINCT;
