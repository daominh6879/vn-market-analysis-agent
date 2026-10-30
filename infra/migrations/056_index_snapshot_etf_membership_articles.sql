-- 056_index_snapshot_etf_membership_articles.sql — the data vn-portfolio's rule engine still lacked after 055:
--   index_snapshot_1425       index board at 14:25 every session (level, open/high/low, matched value, breadth,
--                             foreign) — VNMID / VNSML have no minute history, so this is stored day by day
--   etf_holdings              ETF portfolios read from the funds' monthly reports (kind 'holdings') and the daily
--                             creation basket (kind 'basket'; a basket is not the portfolio, kept apart)
--   index_membership_events   index reviews (VN30, VN100, VNDiamond…) read from news: added / removed and effective date
--   web_articles              news articles fetched from public sitemaps for macro / index facts (interbank rates,
--                             index reviews), with their extraction status

CREATE TABLE IF NOT EXISTS index_snapshot_1425 (
    index_code      TEXT        NOT NULL,
    date            DATE        NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL,
    open            NUMERIC,
    high            NUMERIC,
    low             NUMERIC,
    price           NUMERIC,                -- index level at capture
    prev_close      NUMERIC,
    volume          BIGINT,                 -- matched shares (excl. block trades)
    value           NUMERIC,                -- matched value VND (excl. block trades)
    value_pt        NUMERIC,                -- block-trade value VND
    advances        INTEGER,
    declines        INTEGER,
    unchanged       INTEGER,
    foreign_buy_qty  BIGINT,
    foreign_sell_qty BIGINT,
    source          TEXT        NOT NULL,
    PRIMARY KEY (index_code, date)
);

CREATE TABLE IF NOT EXISTS etf_holdings (
    fund            TEXT    NOT NULL,       -- ETF ticker (E1VFVN30, FUESSV30…)
    as_of           DATE    NOT NULL,
    kind            TEXT    NOT NULL,       -- holdings (monthly report) | basket (daily creation basket)
    ticker          TEXT    NOT NULL,
    weight          NUMERIC,                -- fraction of NAV (holdings) or of the basket value
    shares          BIGINT,
    source          TEXT    NOT NULL,       -- pdf URL or page
    quote           TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (fund, as_of, kind, ticker)
);

CREATE TABLE IF NOT EXISTS web_articles (
    url             TEXT        PRIMARY KEY,
    site            TEXT        NOT NULL,
    topic           TEXT        NOT NULL,   -- interbank | index_review
    published_at    TIMESTAMPTZ,
    title           TEXT,
    body            TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ,
    read_status     TEXT                    -- ok | no_fact | fetch_error | llm_error
);
CREATE INDEX IF NOT EXISTS idx_web_articles_topic ON web_articles (topic, published_at);

CREATE TABLE IF NOT EXISTS index_membership_events (
    index_code      TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    action          TEXT    NOT NULL,       -- add | remove
    effective_date  DATE    NOT NULL,       -- first session of the new basket
    announced_at    TIMESTAMPTZ NOT NULL,   -- article time (when it became known)
    source          TEXT    NOT NULL,       -- article URL
    quote           TEXT,
    model           TEXT,
    PRIMARY KEY (index_code, ticker, action, effective_date)
);
