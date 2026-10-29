-- 048_insider_trades.sql — insider / related-party / institutional-holder trades (VCI IQ events DDIND, DDINS, DDRP).
-- Read by vn-portfolio's rule engine through /advisor/news-facts (khung v2.2: 5.4, 9.1-B, 10.2-B, 10.7).
-- `raw` keeps the full source record: the parsed columns are filled from it where the source has the field.

CREATE TABLE IF NOT EXISTS insider_trades (
    id                SERIAL      PRIMARY KEY,
    ticker            TEXT        NOT NULL,
    event_code        TEXT        NOT NULL,              -- DDIND individual insider | DDINS institution | DDRP related person
    title             TEXT        NOT NULL DEFAULT '',
    public_date       DATE,                              -- when the notice was published (point-in-time key)
    start_date        DATE,                              -- trading window / execution start
    end_date          DATE,                              -- trading window / execution end
    raw               JSONB       NOT NULL,
    source            TEXT        NOT NULL DEFAULT 'vnmarket:vci_iq/events',
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, event_code, title, public_date)
);

CREATE INDEX IF NOT EXISTS idx_insider_trades_ticker ON insider_trades (ticker, public_date DESC);
