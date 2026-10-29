-- 055_macro_insurer_intraday_brokers.sql — the last data gaps of vn-portfolio's rule engine (khung v2.2):
--   macro_indicators         CPI / core inflation / credit and deposit growth (Cục Thống kê releases) and
--                            Vietcombank's published 12-month deposit rate (khung 3.3, 3.5, 5.8)
--   fireant_statement_lines  full statement tree as filed (insurers: investment lines → investment yield, risky assets)
--   intraday_snapshot        state at 14:25 and the ATC fill per session (2.3-D entry_mode same_day_atc)
--   broker_reports           broker research list (Simplize): broker, date, target price, PDF
--   broker_forecasts         EPS / NPAT forecasts an LLM read from each report, with the quoted line (6.5-B consensus)

CREATE TABLE IF NOT EXISTS macro_indicators (
    code          TEXT        NOT NULL,     -- cpi_mom, cpi_ytd, cpi_yoy, core_yoy, credit_ytd, credit_ytd_prev,
                                            -- deposit_growth_ytd, deposit_growth_ytd_prev, deposit_12m_vcb
    period_end    DATE        NOT NULL,     -- month end (CPI) or the "tính đến" date (credit) or the rate date
    value         NUMERIC     NOT NULL,     -- percent
    published_at  TIMESTAMPTZ NOT NULL,     -- when the figure became public (post date / rate table update)
    source        TEXT        NOT NULL,     -- nso:<post id> | vcb
    quote         TEXT,                     -- the sentence the figure was read from
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (code, period_end, source)
);

CREATE TABLE IF NOT EXISTS fireant_statement_lines (
    ticker      TEXT     NOT NULL,
    kind        SMALLINT NOT NULL,          -- 1 balance sheet, 2 income statement
    year        INTEGER  NOT NULL,
    quarter     INTEGER  NOT NULL,          -- 0 = annual
    item_id     BIGINT   NOT NULL,
    parent_id   BIGINT,
    level       SMALLINT,
    name        TEXT     NOT NULL,
    value       NUMERIC,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, kind, year, quarter, item_id)
);

CREATE TABLE IF NOT EXISTS intraday_snapshot (
    ticker          TEXT    NOT NULL,
    date            DATE    NOT NULL,
    open            NUMERIC,                -- VND, first matched price of the session
    high_1425       NUMERIC,
    low_1425        NUMERIC,
    price_1425      NUMERIC,                -- last matched price at or before 14:25
    volume_1425     BIGINT,                 -- cumulative matched volume to 14:25 (incl. ATO)
    atc_price       NUMERIC,                -- ATC fill (the 14:45 bar)
    atc_volume      BIGINT,
    close           NUMERIC,
    volume          BIGINT,
    source          TEXT    NOT NULL,       -- dnse 1m bars
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS broker_reports (
    id              BIGINT  PRIMARY KEY,    -- Simplize report id
    ticker          TEXT    NOT NULL,
    broker          TEXT,
    issue_date      DATE,
    title           TEXT,
    target_price    NUMERIC,                -- VND
    recommendation  TEXT,
    pdf_url         TEXT,
    source          TEXT    NOT NULL DEFAULT 'simplize',
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ,            -- forecast extraction done (with or without figures)
    read_status     TEXT                    -- ok | no_forecast | pdf_error | llm_error
);
CREATE INDEX IF NOT EXISTS idx_broker_reports_ticker ON broker_reports (ticker, issue_date DESC);

CREATE TABLE IF NOT EXISTS broker_forecasts (
    report_id     BIGINT  NOT NULL REFERENCES broker_reports(id) ON DELETE CASCADE,
    fiscal_year   INTEGER NOT NULL,
    eps           NUMERIC,                  -- VND per share
    npat_parent   NUMERIC,                  -- VND
    revenue       NUMERIC,                  -- VND
    quote         TEXT,                     -- verbatim text the figures were read from
    model         TEXT,
    extracted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (report_id, fiscal_year)
);
