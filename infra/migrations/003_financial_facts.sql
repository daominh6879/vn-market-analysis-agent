-- Two data paths: financial numbers go to SQL, not vector DB
-- source='pdf'     : extracted from PDF via LLM
-- source='vnstock' : fetched directly from vnstock Finance API
-- Query priority: vnstock > pdf (see extract_facts.query_fact)

CREATE TABLE IF NOT EXISTS financial_facts (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    period      TEXT NOT NULL,           -- "2024", "2023", "Q3/2024"
    report_type TEXT NOT NULL,           -- "standalone" | "consolidated"
    metric_code TEXT NOT NULL,
    value       NUMERIC NOT NULL,
    unit        TEXT NOT NULL DEFAULT 'VND',
    source_file TEXT NOT NULL,
    source_page INT  NOT NULL,
    source      TEXT NOT NULL DEFAULT 'pdf',
    CONSTRAINT financial_facts_unique
        UNIQUE (ticker, period, report_type, metric_code, source)
);

CREATE TABLE IF NOT EXISTS stock_prices (
    id         SERIAL PRIMARY KEY,
    ticker     TEXT    NOT NULL,
    trade_date DATE    NOT NULL,
    close_adj  NUMERIC NOT NULL,         -- adjusted close price
    volume     BIGINT,
    UNIQUE (ticker, trade_date)
);
