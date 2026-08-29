"""
memory/conversation.py — Conversation history CRUD (Bài 28).

load_history(conversation_id, limit=10) → list of {role, content} dicts
save_turn(conversation_id, user_msg, assistant_msg, session_id=None)
create_conversation(user_id, tenant_id) → conversation_id str
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras

from core.config import settings


def _dsn() -> str:
    return (
        f"host=127.0.0.1 port=5432 "
        f"dbname={settings.POSTGRES_DB} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )


@contextmanager
def _conn():
    conn = psycopg2.connect(_dsn())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_conversation(user_id: str, tenant_id: str = "default") -> str:
    """Insert a new conversation row and return its UUID string."""
    cid = str(uuid.uuid4())
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (conversation_id, user_id, tenant_id) VALUES (%s, %s, %s)",
                (cid, user_id, tenant_id),
            )
    return cid


def load_history(conversation_id: str, limit: int = 10) -> list[dict]:
    """Return the last `limit` message pairs as [{role, content}] ordered oldest→newest."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT role, content FROM messages
                WHERE conversation_id = %s
                ORDER BY seq DESC
                LIMIT %s
                """,
                (conversation_id, limit * 2),  # each turn = 2 messages
            )
            rows = cur.fetchall()
    # Reverse so oldest first
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_turn(
    conversation_id: str,
    user_msg: str,
    assistant_msg: str,
    session_id: Optional[str] = None,
) -> None:
    """Persist one (user, assistant) exchange to the messages table."""
    with _conn() as conn:
        with conn.cursor() as cur:
            for role, content in [("user", user_msg), ("assistant", assistant_msg)]:
                cur.execute(
                    """
                    INSERT INTO messages (message_id, conversation_id, role, content, agent_session_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), conversation_id, role, content, session_id),
                )


def list_conversations(user_id: str, tenant_id: str = "default", limit: int = 50) -> list[dict]:
    """Return all conversations for a user, newest first, with title (first user message)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    c.conversation_id,
                    c.created_at,
                    (
                        SELECT content FROM messages
                        WHERE conversation_id = c.conversation_id AND role = 'user'
                        ORDER BY seq ASC LIMIT 1
                    ) AS title,
                    (
                        (SELECT COUNT(*) FROM messages
                        WHERE conversation_id = c.conversation_id) / 2.0
                    )::int AS turn_count
                FROM conversations c
                WHERE c.user_id = %s AND c.tenant_id = %s
                ORDER BY c.created_at DESC
                LIMIT %s
                """,
                (user_id, tenant_id, limit),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_latest_conversation(user_id: str, tenant_id: str = "default") -> Optional[dict]:
    """Return the most recent conversation row for this user, or None."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT conversation_id, user_id, tenant_id, created_at
                FROM conversations
                WHERE user_id = %s AND tenant_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, tenant_id),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def get_conversation(conversation_id: str) -> Optional[dict]:
    """Return conversation row or None."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT conversation_id, user_id, tenant_id, created_at FROM conversations WHERE conversation_id = %s",
                (conversation_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def delete_conversation(conversation_id: str) -> bool:
    """Delete conversation, all its messages, and episodic Qdrant point. Return True if row existed."""
    from memory.episodic import delete_episode  # local import avoids circular deps

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conversation_id,))
            cur.execute("DELETE FROM conversations WHERE conversation_id = %s", (conversation_id,))
            found = cur.rowcount > 0

    if found:
        try:
            delete_episode(conversation_id)
        except Exception:
            pass  # Qdrant unavailable — Postgres already cleaned up

    return found
