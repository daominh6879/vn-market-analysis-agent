"""
api/main.py — FastAPI entry point.

Run:
    python -m uvicorn api.main:app --reload --port 8027

Endpoints:
    GET  /health
    GET  /sessions/pending
    POST /sessions/{id}/approve
    POST /sessions/{id}/reject
    POST /conversations
    POST /conversations/{id}/turn
    POST /conversations/{id}/messages/stream
    GET  /conversations/{id}/history
    DELETE /conversations/{id}
    GET  /users/{user_id}/conversations
    GET  /users/{user_id}/latest-conversation
    GET  /users/{user_id}/memory
    DELETE /users/{user_id}/memory/{item_id}
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from agents.checkpointer import expire_old_sessions
from api.conversations import router as conversations_router
from api.sessions import router as sessions_router

log = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────────────

_EXPIRE_INTERVAL = 60  # seconds between session expiry sweeps

# ── background tasks ──────────────────────────────────────────────────────────

async def _timeout_loop() -> None:
    """Expire stale pending sessions on a fixed interval."""
    while True:
        try:
            n = expire_old_sessions()
            if n:
                log.info("Expired %d session(s)", n)
        except Exception as exc:
            log.warning("expire_old_sessions failed: %s", exc)
        await asyncio.sleep(_EXPIRE_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_timeout_loop())
    yield
    task.cancel()


# ── app factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    _app = FastAPI(
        title="Agent Approval API",
        description="Human-in-the-loop approval for financial analysis agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    _app.include_router(sessions_router)
    _app.include_router(conversations_router)

    @_app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    return _app


app = create_app()
