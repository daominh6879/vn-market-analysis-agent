-- Enrich securities with curated metadata for name->ticker resolution and
-- data-driven sector lookup (replaces the hardcoded SECTOR_ALIAS in core/tickers.py).
ALTER TABLE securities
    ADD COLUMN IF NOT EXISTS name_aliases TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS tags         TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_sec_tags ON securities USING GIN (tags);
