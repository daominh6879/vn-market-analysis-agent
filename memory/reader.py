"""
memory/reader.py — Read and write user_memory records (Bài 28).

load_user_memory(user_id, tenant_id, max_items=5) → list[dict]
save_memory_item(user_id, tenant_id, key, value, confidence, source_message) → str (new id)

Rules:
- Only active records (superseded_by IS NULL) are returned.
- Sorted by confidence DESC, limited to max_items.
- When saving, mark any existing active record with the same key as superseded_by=new_id.
  The old record is NEVER deleted — retained for audit.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras
import json

from core.config import settings


def _dsn() -> str:
    return (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
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


def load_user_memory(
    user_id: str,
    tenant_id: str = "default",
    max_items: int = 5,
) -> list[dict]:
    """Return active (non-superseded) memory items, highest confidence first."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, key, value, confidence, source_message
                FROM user_memory
                WHERE user_id = %s AND tenant_id = %s AND superseded_by IS NULL
                ORDER BY confidence DESC
                LIMIT %s
                """,
                (user_id, tenant_id, max_items),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def save_memory_item(
    user_id: str,
    tenant_id: str,
    key: str,
    value: Any,
    confidence: float,
    source_message: str = "",
) -> str:
    """Insert new memory item. If an active record with same key exists, mark it superseded."""
    new_id = str(uuid.uuid4())
    with _conn() as conn:
        with conn.cursor() as cur:
            # Find existing active record for this user+key
            cur.execute(
                """
                SELECT id FROM user_memory
                WHERE user_id = %s AND tenant_id = %s AND key = %s AND superseded_by IS NULL
                """,
                (user_id, tenant_id, key),
            )
            existing = cur.fetchone()

            # Insert new record
            cur.execute(
                """
                INSERT INTO user_memory (id, tenant_id, user_id, key, value, confidence, source_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_id,
                    tenant_id,
                    user_id,
                    key,
                    json.dumps(value) if not isinstance(value, str) else json.dumps(value),
                    confidence,
                    source_message,
                ),
            )

            # Mark old record superseded (after insert so FK is valid)
            if existing:
                cur.execute(
                    "UPDATE user_memory SET superseded_by = %s WHERE id = %s",
                    (new_id, existing[0]),
                )
    return new_id


def delete_memory_item(item_id: str, user_id: str) -> bool:
    """Hard-delete one active memory item. Return True if row existed."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_memory WHERE id = %s AND user_id = %s",
                (item_id, user_id),
            )
            return cur.rowcount > 0
