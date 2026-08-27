-- Broker research views: price targets, support/resistance from CTCK (Phase 5).
-- Extracted via LLM from news_articles — one row per (broker, subject, date).

CREATE TABLE IF NOT EXISTS broker_views (
    id              SERIAL      PRIMARY KEY,
    broker          TEXT        NOT NULL,    -- 'TPS' | 'VCBS' | 'Yuanta' | 'VNDirect' | ...
    ticker_or_index TEXT        NOT NULL,    -- ticker 'HPG' or index 'VNINDEX'
    published_at    TIMESTAMPTZ NOT NULL,
    stance          TEXT,                    -- 'buy' | 'sell' | 'neutral' | 'accumulate' | 'reduce'
    target          NUMERIC,                 -- giá / điểm mục tiêu
    support         NUMERIC,                 -- vùng hỗ trợ
    resistance      NUMERIC,                 -- vùng kháng cự
    source_url      TEXT        NOT NULL DEFAULT '',
    news_article_id INT         REFERENCES news_articles(id) ON DELETE SET NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (broker, ticker_or_index, published_at)
);

CREATE INDEX IF NOT EXISTS idx_bv_subject  ON broker_views (ticker_or_index, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_bv_broker   ON broker_views (broker, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_bv_date     ON broker_views (published_at DESC);
