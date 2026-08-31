"""
api/sessions.py — Human-approval endpoints for Bài 27.

GET  /sessions/pending          — list pending (not expired) sessions
POST /sessions/{id}/approve     — resume graph with approval
POST /sessions/{id}/reject      — reject and mark done
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.checkpointer import PostgresCheckpointer, _conn, load_checkpoint
from agents.graph import build_graph
from langgraph.types import Command

router = APIRouter(prefix="/sessions", tags=["sessions"])


class ApproveBody(BaseModel):
    edits: Optional[dict[str, Any]] = None


def _update_status(session_id: str, status: str, note: str = "") -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_sessions
                SET status=%s,
                    audit_log = audit_log || jsonb_build_array(
                        jsonb_build_object('action', %s, 'at', NOW()::text)
                    )
                WHERE session_id=%s
                """,
                (status, note or status, session_id),
            )


def _get_pending(session_id: str) -> dict:
    row = load_checkpoint(session_id)
    if not row:
        raise HTTPException(404, f"Session {session_id} not found")
    if row["status"] == "expired":
        raise HTTPException(410, "Session expired")
    if row["status"] != "pending":
        raise HTTPException(409, f"Session status is '{row['status']}', not pending")
    if row["expires_at"] < datetime.now(timezone.utc):
        _update_status(session_id, "expired")
        raise HTTPException(410, "Session expired")
    return row


@router.get("/pending")
def list_pending():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT session_id, ticker, status, created_at, expires_at,
                       state->>'risk_verdict' AS risk_verdict
                FROM agent_sessions
                WHERE status='pending' AND expires_at > NOW()
                ORDER BY created_at DESC
                """,
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"sessions": rows}


@router.post("/{session_id}/approve")
def approve_session(session_id: str, body: ApproveBody = ApproveBody()):
    row = _get_pending(session_id)
    _update_status(session_id, "approved", "approved")

    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer, human_approval=True)
    thread_config = {"configurable": {"thread_id": session_id}}
    update = body.edits or {}
    final = app.invoke(
        Command(resume=True, update=update if update else None),
        config=thread_config,
    )
    report = final.get("report") or "[Không có báo cáo]"
    _update_status(session_id, "completed", "completed")
    return {
        "session_id": session_id,
        "ticker": final.get("ticker"),
        "risk_verdict": final.get("risk_verdict"),
        "report": report,
    }


@router.post("/{session_id}/reject")
def reject_session(session_id: str):
    row = _get_pending(session_id)
    checkpointer = PostgresCheckpointer()
    app = build_graph(checkpointer=checkpointer, human_approval=True)
    thread_config = {"configurable": {"thread_id": session_id}}
    app.invoke(Command(resume=False), config=thread_config)
    _update_status(session_id, "rejected", "rejected")
    return {"session_id": session_id, "status": "rejected"}
