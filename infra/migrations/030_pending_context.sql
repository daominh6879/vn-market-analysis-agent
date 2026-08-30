-- Bài 32+: General clarification context for unresolvable queries
-- Stores what was unclear and what the bot already knows, so the next turn can resume.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS pending_context JSONB DEFAULT NULL;
