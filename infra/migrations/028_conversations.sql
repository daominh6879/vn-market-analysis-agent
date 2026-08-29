-- Bài 28: Conversation history + user memory

-- One logical conversation session (many turns)
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id, tenant_id);

-- Per-turn messages
CREATE TABLE IF NOT EXISTS messages (
    seq                BIGSERIAL,
    message_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id    UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content            TEXT NOT NULL,
    agent_session_id   TEXT REFERENCES agent_sessions(session_id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, seq);

-- Extracted user preferences (never deleted, superseded instead)
CREATE TABLE IF NOT EXISTS user_memory (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    user_id          TEXT NOT NULL,
    key              TEXT NOT NULL,
    value            JSONB NOT NULL,
    confidence       FLOAT NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source_message   TEXT,
    superseded_by    UUID REFERENCES user_memory(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory (user_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_memory_active ON user_memory (user_id, tenant_id)
    WHERE superseded_by IS NULL;
