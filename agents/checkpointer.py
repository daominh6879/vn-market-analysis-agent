"""
agents/checkpointer.py — Postgres-backed LangGraph checkpointer for Bài 27.

Two concerns:
  1. PostgresCheckpointer — BaseCheckpointSaver subclass that stores LangGraph
     checkpoint data in lg_checkpoints / lg_checkpoint_blobs / lg_checkpoint_writes.
  2. save_checkpoint / load_checkpoint — helpers that persist AgentState snapshots
     to agent_sessions (used by the approval API).
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional, Sequence

import psycopg2
import psycopg2.extras

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.config import settings


# ── DB connection ─────────────────────────────────────────────────────────────

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


# ── PostgresCheckpointer ──────────────────────────────────────────────────────

class PostgresCheckpointer(BaseCheckpointSaver):
    """Minimal Postgres-backed LangGraph checkpointer.

    Stores checkpoint binary data in:
      - lg_checkpoints
      - lg_checkpoint_blobs
      - lg_checkpoint_writes
    """

    def __init__(self) -> None:
        super().__init__(serde=JsonPlusSerializer())

    # ── helpers ───────────────────────────────────────────────────────────────

    def _save_blobs(
        self,
        cur: Any,
        thread_id: str,
        ns: str,
        new_versions: dict[str, Any],
        channel_values: dict[str, Any],
    ) -> None:
        for channel, version in new_versions.items():
            if channel in channel_values:
                blob_type, blob = self.serde.dumps_typed(channel_values[channel])
            else:
                blob_type, blob = "empty", b""
            cur.execute(
                """
                INSERT INTO lg_checkpoint_blobs
                    (thread_id, checkpoint_ns, channel, version, blob_type, blob)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, checkpoint_ns, channel, version)
                DO UPDATE SET blob_type=EXCLUDED.blob_type, blob=EXCLUDED.blob
                """,
                (thread_id, ns, channel, str(version), blob_type, psycopg2.Binary(blob)),
            )

    def _load_blobs(
        self,
        cur: Any,
        thread_id: str,
        ns: str,
        channel_versions: dict[str, Any],
    ) -> dict[str, Any]:
        if not channel_versions:
            return {}
        rows = []
        for channel, version in channel_versions.items():
            cur.execute(
                "SELECT channel, blob_type, blob FROM lg_checkpoint_blobs "
                "WHERE thread_id=%s AND checkpoint_ns=%s AND channel=%s AND version=%s",
                (thread_id, ns, channel, str(version)),
            )
            row = cur.fetchone()
            if row:
                rows.append(row)
        result = {}
        for channel, blob_type, blob in rows:
            if blob_type == "empty":
                continue
            result[channel] = self.serde.loads_typed((blob_type, bytes(blob)))
        return result

    def _load_writes(
        self, cur: Any, thread_id: str, ns: str, checkpoint_id: str
    ) -> list[tuple]:
        cur.execute(
            "SELECT task_id, channel, blob_type, blob FROM lg_checkpoint_writes "
            "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s "
            "ORDER BY idx",
            (thread_id, ns, checkpoint_id),
        )
        rows = cur.fetchall()
        return [
            (task_id, channel, self.serde.loads_typed((blob_type, bytes(blob))))
            for task_id, channel, blob_type, blob in rows
        ]

    # ── BaseCheckpointSaver interface ─────────────────────────────────────────

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        cp = checkpoint.copy()
        channel_values: dict = cp.pop("channel_values", {})  # type: ignore[misc]
        parent_id = config["configurable"].get("checkpoint_id")

        cp_bytes_type, cp_bytes = self.serde.dumps_typed(cp)
        meta_bytes_type, meta_bytes = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        # Pack type+bytes together as b"{type}\n{bytes}"
        cp_blob = cp_bytes_type.encode() + b"\n" + cp_bytes
        meta_blob = meta_bytes_type.encode() + b"\n" + meta_bytes

        with _conn() as conn:
            with conn.cursor() as cur:
                self._save_blobs(cur, thread_id, ns, new_versions, channel_values)
                cur.execute(
                    """
                    INSERT INTO lg_checkpoints
                        (thread_id, checkpoint_ns, checkpoint_id,
                         parent_checkpoint_id, checkpoint_data, metadata_data)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
                    DO UPDATE SET
                        parent_checkpoint_id=EXCLUDED.parent_checkpoint_id,
                        checkpoint_data=EXCLUDED.checkpoint_data,
                        metadata_data=EXCLUDED.metadata_data
                    """,
                    (
                        thread_id, ns, checkpoint["id"],
                        parent_id,
                        psycopg2.Binary(cp_blob),
                        psycopg2.Binary(meta_blob),
                    ),
                )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with _conn() as conn:
            with conn.cursor() as cur:
                for idx, (channel, value) in enumerate(writes):
                    real_idx = WRITES_IDX_MAP.get(channel, idx)
                    blob_type, blob = self.serde.dumps_typed(value)
                    cur.execute(
                        """
                        INSERT INTO lg_checkpoint_writes
                            (thread_id, checkpoint_ns, checkpoint_id,
                             task_id, idx, channel, blob_type, blob, task_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                        DO UPDATE SET
                            channel=EXCLUDED.channel,
                            blob_type=EXCLUDED.blob_type,
                            blob=EXCLUDED.blob,
                            task_path=EXCLUDED.task_path
                        """,
                        (
                            thread_id, ns, checkpoint_id,
                            task_id, real_idx, channel,
                            blob_type, psycopg2.Binary(blob), task_path,
                        ),
                    )

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        with _conn() as conn:
            with conn.cursor() as cur:
                if checkpoint_id:
                    cur.execute(
                        "SELECT checkpoint_id, parent_checkpoint_id, "
                        "checkpoint_data, metadata_data "
                        "FROM lg_checkpoints "
                        "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s",
                        (thread_id, ns, checkpoint_id),
                    )
                else:
                    cur.execute(
                        "SELECT checkpoint_id, parent_checkpoint_id, "
                        "checkpoint_data, metadata_data "
                        "FROM lg_checkpoints "
                        "WHERE thread_id=%s AND checkpoint_ns=%s "
                        "ORDER BY checkpoint_id DESC LIMIT 1",
                        (thread_id, ns),
                    )
                row = cur.fetchone()
                if not row:
                    return None

                cp_id, parent_id, cp_blob, meta_blob = row
                cp_blob = bytes(cp_blob)
                meta_blob = bytes(meta_blob)

                cp_type, cp_bytes = cp_blob.split(b"\n", 1)
                meta_type, meta_bytes = meta_blob.split(b"\n", 1)

                cp: Checkpoint = self.serde.loads_typed((cp_type.decode(), cp_bytes))  # type: ignore[assignment]
                meta: CheckpointMetadata = self.serde.loads_typed((meta_type.decode(), meta_bytes))  # type: ignore[assignment]

                cp["channel_values"] = self._load_blobs(
                    cur, thread_id, ns, cp.get("channel_versions", {})
                )
                pending_writes = self._load_writes(cur, thread_id, ns, cp_id)

                return CheckpointTuple(
                    config={
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": ns,
                            "checkpoint_id": cp_id,
                        }
                    },
                    checkpoint=cp,
                    metadata=meta,
                    parent_config=(
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": ns,
                                "checkpoint_id": parent_id,
                            }
                        }
                        if parent_id
                        else None
                    ),
                    pending_writes=pending_writes,
                )

    def list(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if not config:
            return
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")

        query = (
            "SELECT checkpoint_id FROM lg_checkpoints "
            "WHERE thread_id=%s AND checkpoint_ns=%s "
            "ORDER BY checkpoint_id DESC"
        )
        params: list = [thread_id, ns]
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                ids = [r[0] for r in cur.fetchall()]

        for cp_id in ids:
            t = self.get_tuple({
                "configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp_id}
            })
            if t:
                yield t


# ── Session state helpers (agent_sessions table) ──────────────────────────────

SESSION_TTL_MINUTES = 5


def save_checkpoint(
    session_id: str,
    ticker: str,
    state: dict,
    status: str = "pending",
) -> None:
    """Persist AgentState snapshot to agent_sessions."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_TTL_MINUTES)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_sessions (session_id, ticker, state, status, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    state=EXCLUDED.state, status=EXCLUDED.status, expires_at=EXCLUDED.expires_at
                """,
                (
                    session_id,
                    ticker,
                    json.dumps(state),
                    status,
                    expires_at,
                ),
            )


def load_checkpoint(session_id: str) -> Optional[dict]:
    """Load AgentState snapshot from agent_sessions. Returns None if not found."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM agent_sessions WHERE session_id=%s",
                (session_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def expire_old_sessions() -> int:
    """Mark expired pending sessions. Returns count updated."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_sessions
                SET status='expired',
                    audit_log = audit_log || jsonb_build_array(
                        jsonb_build_object('action', 'expired', 'at', NOW()::text)
                    )
                WHERE status='pending' AND expires_at < NOW()
                """,
            )
            return cur.rowcount
