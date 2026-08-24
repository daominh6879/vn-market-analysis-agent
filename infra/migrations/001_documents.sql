-- Bài 10: Bảng theo dõi tài liệu đã index
-- Không xoá record — chỉ đánh dấu status='deleted' để kiểm toán

CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'deleted'
    source_uri   TEXT NOT NULL,
    collection   TEXT NOT NULL DEFAULT 'hpg_structural',
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at   TIMESTAMPTZ
);
