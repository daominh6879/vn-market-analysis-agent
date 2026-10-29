-- 050_rule_engine_datasets.sql — datasets vn-portfolio's rule engine (khung v2.2) had switched rules off for
-- (config/data_inventory.yaml there). All come from VNDirect finfo via vnmarket, commodities from yfinance, statement
-- notes from the indexed BCTC PDFs (LLM table reading). Read through /advisor/market-data.

-- 12.4 "giao dịch thỏa thuận lớn bất thường": put-through (pt) vs matched (nm) volume/value per session (VND)
CREATE TABLE IF NOT EXISTS block_trades (
    ticker      TEXT        NOT NULL,
    date        DATE        NOT NULL,
    pt_volume   BIGINT      NOT NULL DEFAULT 0,
    pt_value    NUMERIC     NOT NULL DEFAULT 0,      -- VND
    nm_volume   BIGINT      NOT NULL DEFAULT 0,
    nm_value    NUMERIC     NOT NULL DEFAULT 0,      -- VND
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_block_trades_date ON block_trades (date DESC);

-- 3.3 "P/E thị trường về vùng thấp lịch sử": index P/E and P/B per session (VNINDEX from 2017-12)
CREATE TABLE IF NOT EXISTS index_valuation (
    index_code  TEXT        NOT NULL,
    date        DATE        NOT NULL,
    pe          NUMERIC,
    pb          NUMERIC,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_code, date)
);

-- 7.6-B / 12.5 room ngoại: total room and room left in shares, per session
CREATE TABLE IF NOT EXISTS foreign_room (
    ticker      TEXT        NOT NULL,
    date        DATE        NOT NULL,
    total_room  BIGINT,
    room_left   BIGINT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

-- 12.4 "cơ cấu cổ đông tập trung": major shareholders / insiders as VNDirect lists them. effective_date = date of the
-- holding figure; first_seen_at = when this app first saw the row (point-in-time for rows without a date).
CREATE TABLE IF NOT EXISTS shareholders (
    ticker          TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    holder_type     TEXT        NOT NULL DEFAULT '',     -- PERSON | COMPANY
    role            TEXT        NOT NULL DEFAULT '',
    shares          BIGINT,
    pct             NUMERIC,                             -- percent of shares outstanding
    group_pct       NUMERIC,                             -- percent held with related persons, when VNDirect has it
    effective_date  DATE,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE NULLS NOT DISTINCT (ticker, name, effective_date)
);
CREATE INDEX IF NOT EXISTS idx_shareholders_ticker ON shareholders (ticker);

-- 5.9 dilution (issue price, shares, purpose in note), 12.x trading-status / margin-eligibility alerts, AGM dates
CREATE TABLE IF NOT EXISTS corporate_actions (
    id               TEXT        PRIMARY KEY,           -- VNDirect event id
    ticker           TEXT        NOT NULL,
    action_group     TEXT        NOT NULL DEFAULT '',   -- investorRight | schedEvent | stockAlert
    action_type      TEXT        NOT NULL DEFAULT '',   -- ISSUE, schedIssue, MEETING, alert, noticed, nomargin...
    type_desc        TEXT        NOT NULL DEFAULT '',
    note             TEXT        NOT NULL DEFAULT '',
    price            NUMERIC,                           -- issue price VND/share
    ratio            NUMERIC,                           -- percent
    shares           BIGINT,
    disclosure_date  DATE,
    effective_date   DATE,
    expired_date     DATE,
    actual_date      DATE,
    trading_start    DATE,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_corp_actions_ticker ON corporate_actions (ticker, disclosure_date);
CREATE INDEX IF NOT EXISTS idx_corp_actions_type ON corporate_actions (action_type);

-- VNDirect line items not in financial_facts: AGM business plan (model 33), broker loans (89), insurer statements
-- (411/412). created = when VNDirect first stored the figure (point-in-time for plans).
CREATE TABLE IF NOT EXISTS vnd_financial_items (
    ticker       TEXT        NOT NULL,
    fiscal_date  DATE        NOT NULL,
    report_type  TEXT        NOT NULL,                  -- QUARTER | ANNUAL | ESTIMATION ...
    model_type   INTEGER     NOT NULL,
    item_code    INTEGER     NOT NULL,
    value        NUMERIC,
    created      TIMESTAMP,
    modified     TIMESTAMP,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, fiscal_date, report_type, model_type, item_code)
);

-- 4.4 / 9.6 ngành chu kỳ: output commodity prices (yfinance futures, front month close)
CREATE TABLE IF NOT EXISTS commodity_daily (
    code        TEXT        NOT NULL,                   -- brent, hrc, iron_ore, natgas, urea...
    date        DATE        NOT NULL,
    close       NUMERIC     NOT NULL,
    unit        TEXT        NOT NULL DEFAULT '',
    source      TEXT        NOT NULL DEFAULT 'yfinance',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (code, date)
);

-- 5.5 / 5.6 / 5.8: figures that only exist in the notes of the annual BCTC (related-party receivables and loans,
-- bonds maturing within 12 months, bank loan groups 1–5 and demand deposits). Read from the indexed PDF chunks by the
-- LLM (table → typed rows), one row per metric; source_file/page say where.
CREATE TABLE IF NOT EXISTS bctc_note_facts (
    ticker        TEXT        NOT NULL,
    period        TEXT        NOT NULL,                 -- fiscal year, e.g. 2025
    metric_code   TEXT        NOT NULL,
    value         NUMERIC     NOT NULL,                 -- VND
    source_file   TEXT        NOT NULL DEFAULT '',
    source_page   INTEGER,
    quote         TEXT        NOT NULL DEFAULT '',      -- row label as written in the report
    extracted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, period, metric_code)
);
