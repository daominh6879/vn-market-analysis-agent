-- Ticker Trend Analyzer (trend/, plan: vn-portfolio docs/plan.md). Prices, indices and articles already live in
-- ohlcv_daily, market_index_daily and news_articles; these tables hold what is derived from them.

-- Feature snapshot of one ticker on one session, computed from data up to that session's close only.
CREATE TABLE IF NOT EXISTS daily_features (
    ticker          TEXT        NOT NULL,
    session         DATE        NOT NULL,
    feature_version TEXT        NOT NULL,
    features        JSONB       NOT NULL,             -- raw values (fractions), market / sector context
    signals         JSONB       NOT NULL,             -- categorical reading of the features
    quality         JSONB       NOT NULL,             -- data-quality flags
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, session, feature_version)
);

-- One event per cluster of articles about the same thing for one ticker. Extracted from the cluster's first
-- article only, so nothing in the record was published after available_at.
CREATE TABLE IF NOT EXISTS news_events (
    id                  SERIAL      PRIMARY KEY,
    cluster_key         TEXT        NOT NULL UNIQUE,  -- '<ticker>:<first article id>'
    ticker              TEXT        NOT NULL,
    article_ids         INTEGER[]   NOT NULL,         -- first article first; later reprints appended
    available_at        TIMESTAMPTZ NOT NULL,         -- first article's published_at
    source_type         TEXT        NOT NULL,         -- 'official' | 'press'
    event_type          TEXT        NOT NULL,
    facts               JSONB       NOT NULL,         -- list of short factual statements
    direction           TEXT        NOT NULL,         -- 'positive' | 'negative' | 'neutral' (for the business)
    impact_channel      TEXT        NOT NULL,
    impact_horizon      TEXT        NOT NULL,         -- 'short_term' | 'medium_term' | 'long_term'
    novelty             TEXT        NOT NULL,         -- 'high' | 'medium' | 'low'
    materiality         TEXT        NOT NULL,         -- 'high' | 'medium' | 'low'
    verification_status TEXT        NOT NULL,         -- 'verified' | 'unverified' | 'conflicting'
    relevance           TEXT        NOT NULL,         -- 'direct' | 'indirect' | 'none' (feed tags are loose)
    evidence_url        TEXT,
    model               TEXT,
    prompt_version      TEXT        NOT NULL,
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_news_events_ticker ON news_events (ticker, available_at DESC);

-- One Trend Judge call (or a baseline) for one ticker on one session: input snapshot, versions, output.
CREATE TABLE IF NOT EXISTS forecast_runs (
    id              SERIAL      PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    session         DATE        NOT NULL,             -- data session (as of its close)
    as_of           TIMESTAMPTZ NOT NULL,
    variant         TEXT        NOT NULL,             -- 'A' rules | 'B' ML | 'C' LLM + events | 'D' quant + LLM
    mode            TEXT        NOT NULL,             -- 'live' | 'backtest'
    horizons        INTEGER[]   NOT NULL,
    feature_version TEXT,
    prompt_version  TEXT,
    model           TEXT,
    input           JSONB       NOT NULL,
    output          JSONB       NOT NULL,             -- {forecasts: [{horizon, absolute_trend, ...}], ...}
    valid           BOOLEAN     NOT NULL,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, session, variant, mode, prompt_version)
);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_session ON forecast_runs (session DESC, variant);

-- Realised outcome of a signal on (ticker, session): buy at the next session's open, sell at the close h sessions
-- after the signal, after fees and tax; alpha against VNINDEX over the same open -> close.
CREATE TABLE IF NOT EXISTS forecast_labels (
    ticker         TEXT    NOT NULL,
    session        DATE    NOT NULL,
    horizon        INTEGER NOT NULL,
    label_version  TEXT    NOT NULL,
    entry_date     DATE    NOT NULL,
    exit_date      DATE    NOT NULL,
    net_return     NUMERIC NOT NULL,
    index_return   NUMERIC NOT NULL,
    alpha          NUMERIC NOT NULL,
    absolute_label TEXT    NOT NULL,                  -- 'bullish' | 'neutral' | 'bearish'
    relative_label TEXT    NOT NULL,
    fillable       BOOLEAN NOT NULL,                  -- false when the entry open sat at the ceiling
    PRIMARY KEY (ticker, session, horizon, label_version)
);

-- One walk-forward evaluation: configuration, metrics per variant / horizon, calibration tables.
CREATE TABLE IF NOT EXISTS backtest_runs (
    id         SERIAL      PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    config     JSONB       NOT NULL,
    metrics    JSONB       NOT NULL,
    calibration JSONB,
    notes      TEXT
);
