"""
api/main.py — FastAPI entry point for Bài 27 approval API.

Run:
    python -m uvicorn api.main:app --reload --port 8027

Endpoints:
    GET  /sessions/pending
    POST /sessions/{id}/approve
    POST /sessions/{id}/reject
"""

from __future__ import annotations

import asyncio
from dotenv import load_dotenv
load_dotenv()
import logging

from fastapi import FastAPI
from contextlib import asynccontextmanager

from agents.checkpointer import expire_old_sessions
from api.sessions import router

log = logging.getLogger(__name__)


async def _timeout_loop() -> None:
    """Background task: expire stale pending sessions every 60 seconds."""
    while True:
        try:
            n = expire_old_sessions()
            if n:
                log.info("Expired %d session(s)", n)
        except Exception as exc:
            log.warning("expire_old_sessions failed: %s", exc)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_timeout_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Agent Approval API",
    description="Human-in-the-loop approval for financial analysis agent (Bài 27)",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
