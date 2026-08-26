-- News articles from CafeF / VnExpress RSS
-- URL is the dedup key — upsert-safe, re-running pipeline is idempotent
-- tickers: extracted via pattern match from title (no LLM needed)

CREATE TABLE IF NOT EXISTS news_articles (
    id           SERIAL PRIMARY KEY,
    url          TEXT         NOT NULL UNIQUE,
    title        TEXT         NOT NULL,
    body         TEXT         NOT NULL,
    source       TEXT         NOT NULL,        -- 'cafef' | 'vnexpress'
    published_at TIMESTAMPTZ  NOT NULL,
    indexed_at   TIMESTAMPTZ,                  -- NULL until embedded into news_chunks
    tickers      TEXT[]       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_tickers   ON news_articles USING GIN (tickers);
CREATE INDEX IF NOT EXISTS idx_news_source    ON news_articles (source);
CREATE INDEX IF NOT EXISTS idx_news_unindexed ON news_articles (id) WHERE indexed_at IS NULL;
