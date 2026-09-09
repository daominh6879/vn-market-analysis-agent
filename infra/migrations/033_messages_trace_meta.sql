-- Bài 33: per-message trace + graph metadata for the observability dock.
-- Stores the graph node path (and future trace steps) as JSONB on each assistant
-- message so historical messages can render their trace + workflow graph.

ALTER TABLE messages ADD COLUMN IF NOT EXISTS trace_meta JSONB;
