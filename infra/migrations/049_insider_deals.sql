-- 049_insider_deals.sql — insider / related-person / major-holder deals with volumes (CafeF GDCoDong).
-- Read by vn-portfolio's rule engine through /advisor/news-facts (khung v2.2 5.4 net buying/selling, 10.7 thresholds:
-- % of shares, key person selling ≥ 20% of holding). VCI notices (insider_trades) have no volumes; this table does.
-- One row per registration; CafeF fills the executed volumes later, so a row seen again is updated in place.

CREATE TABLE IF NOT EXISTS insider_deals (
    id                SERIAL      PRIMARY KEY,
    ticker            TEXT        NOT NULL,
    holder_code       TEXT        NOT NULL DEFAULT '',      -- CafeF ShareHolderCode (CEO_…, LQ_… related person)
    person            TEXT        NOT NULL DEFAULT '',
    position          TEXT        NOT NULL DEFAULT '',      -- role of the person (or relation for a related person)
    related_person    TEXT        NOT NULL DEFAULT '',
    related_position  TEXT        NOT NULL DEFAULT '',      -- role of the insider a related person is linked to
    volume_before     BIGINT,
    volume_after      BIGINT,
    ownership_pct     NUMERIC,
    plan_buy          BIGINT,
    plan_sell         BIGINT,
    plan_begin        DATE,
    plan_end          DATE,
    real_buy          BIGINT,
    real_sell         BIGINT,
    real_end          DATE,
    published_at      TIMESTAMPTZ,
    note              TEXT        NOT NULL DEFAULT '',
    source            TEXT        NOT NULL DEFAULT 'cafef:GDCoDong',
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE NULLS NOT DISTINCT (ticker, holder_code, person, plan_begin, plan_end)
);

CREATE INDEX IF NOT EXISTS idx_insider_deals_ticker ON insider_deals (ticker, published_at DESC);
