-- Index for common query pattern: filter by ticker + metric_code + period
-- Fixes full table scan on financial_facts (2,305+ rows)
CREATE INDEX IF NOT EXISTS idx_ff_ticker_metric_period
    ON financial_facts(ticker, metric_code, period);
