-- Daily bars of the buy-and-hold benchmark(s) the portfolio is judged against (pipeline/assets_benchmark.py).
-- Kept out of ohlcv_daily: many readers take every ticker there (screeners, universes), and an ETF is not a candidate.
CREATE TABLE IF NOT EXISTS benchmark_daily (
    ticker     TEXT        NOT NULL,
    date       DATE        NOT NULL,
    open       NUMERIC     NOT NULL,             -- thousand VND, like ohlcv_daily
    high       NUMERIC     NOT NULL,
    low        NUMERIC     NOT NULL,
    close      NUMERIC     NOT NULL,
    volume     BIGINT      NOT NULL DEFAULT 0,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);
