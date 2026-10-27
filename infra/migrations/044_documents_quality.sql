-- Index-time sanity check of OCR'd BCTC (core/balance_check.py): total assets must equal total capital.
-- NULL = not checked yet; ingest/sync_bctc.ensure_quality fills it for new and existing documents.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS quality        TEXT,    -- 'ok' | 'mismatch' | 'unverified'
    ADD COLUMN IF NOT EXISTS quality_detail JSONB;
