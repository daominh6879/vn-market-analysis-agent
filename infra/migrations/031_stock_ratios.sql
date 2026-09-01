-- Fundamental valuation ratios refreshed daily by vnstock/KBS (Dagster asset).
-- One row per ticker — upsert on ticker keeps only latest snapshot.
-- fetched_at lets intent code detect staleness (> 48h = fallback to live call).

CREATE TABLE IF NOT EXISTS stock_ratios (
    ticker              VARCHAR(10)  PRIMARY KEY,
    fetched_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    pe                  NUMERIC,
    pb                  NUMERIC,
    roe_pct             NUMERIC,
    roa_pct             NUMERIC,
    eps                 NUMERIC,
    gross_margin_pct    NUMERIC,
    net_margin_pct      NUMERIC,
    revenue_growth_pct  NUMERIC,
    earnings_growth_pct NUMERIC,
    de_ratio            NUMERIC,
    ev_ebitda           NUMERIC
);
