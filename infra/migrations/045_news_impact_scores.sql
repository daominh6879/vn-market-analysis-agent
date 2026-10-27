-- LLM judgement of what an article means for one ticker (portfolio/news_scorer.py). Written the first time a pair is
-- scored (advice run, or the event-study backfill) and never updated, so the score is what was knowable then and
-- the event study (portfolio/backtest_news.py) can test it against later prices.
CREATE TABLE IF NOT EXISTS news_impact_scores (
    article_id  INTEGER     NOT NULL REFERENCES news_articles (id) ON DELETE CASCADE,
    ticker      TEXT        NOT NULL,
    kind        TEXT        NOT NULL,                 -- 'ticker' | 'sector' | 'related'
    channel     TEXT,                                 -- impact channel label (related) / sector label
    score       SMALLINT    NOT NULL,                 -- 0 unrelated .. 3 direct, material impact on this company
    direction   SMALLINT    NOT NULL,                 -- -1 negative | 0 neutral / unclear | 1 positive
    magnitude   SMALLINT,                             -- 1 small | 2 medium | 3 large
    impact      TEXT,                                 -- one line: channel and direction for this company
    model       TEXT,
    origin      TEXT        NOT NULL,                 -- 'advice' | 'backfill'
    scored_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (article_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_news_impact_ticker ON news_impact_scores (ticker);
