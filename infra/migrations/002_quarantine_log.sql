-- Bài 11: Bảng lưu lý do cách ly file rác
-- Không index những file này vào Qdrant

CREATE TABLE IF NOT EXISTS quarantine_log (
    id           SERIAL PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    source_path  TEXT NOT NULL,
    reason       TEXT NOT NULL,
    char_ratio   NUMERIC,
    chars_per_page NUMERIC,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quarantine_doc_id ON quarantine_log(doc_id);
