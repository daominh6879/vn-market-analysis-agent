"""
api/conversations.py — Conversation + memory endpoints for Bài 28.

POST /conversations             → create conversation, return conversation_id
POST /conversations/{id}/turn  → run one turn with history + user memory
GET  /conversations/{id}/history → list messages
GET  /users/{user_id}/memory   → list active user memory items
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from memory.conversation import (
    create_conversation,
    get_conversation,
    get_latest_conversation,
    list_conversations,
    load_history,
)
from memory.reader import load_user_memory
from memory.turn_handler import run_turn, stream_turn

router = APIRouter(tags=["conversations"])


class CreateConversationBody(BaseModel):
    user_id: str
    tenant_id: str = "default"


class TurnBody(BaseModel):
    user_id: str
    tenant_id: str = "default"
    message: str


class MessageRequest(BaseModel):
    user_id: str
    tenant_id: str = "default"
    message: str
    is_first_turn: bool = False


# ── conversations ─────────────────────────────────────────────────────────────

@router.get("/users/{user_id}/conversations")
def user_conversations(user_id: str, tenant_id: str = "default", limit: int = 50):
    convs = list_conversations(user_id, tenant_id, limit)
    return {"conversations": convs}


@router.get("/users/{user_id}/latest-conversation")
def latest_conversation(user_id: str, tenant_id: str = "default"):
    conv = get_latest_conversation(user_id, tenant_id)
    if not conv:
        return {"conversation_id": None, "messages": []}
    messages = load_history(conv["conversation_id"], limit=20)
    return {"conversation_id": conv["conversation_id"], "messages": messages}


@router.post("/conversations")
def create_conv(body: CreateConversationBody):
    cid = create_conversation(body.user_id, body.tenant_id)
    return {"conversation_id": cid}


@router.post("/conversations/{conversation_id}/turn")
def conversation_turn(conversation_id: str, body: TurnBody):
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conversation_id} not found")

    reply = run_turn(
        conversation_id=conversation_id,
        user_id=body.user_id,
        user_message=body.message,
        tenant_id=body.tenant_id,
    )
    history = load_history(conversation_id, limit=10)
    return {
        "conversation_id": conversation_id,
        "reply": reply,
        "turn_count": len(history) // 2,
    }


@router.get("/conversations/{conversation_id}/history")
def conversation_history(conversation_id: str, limit: int = 20):
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conversation_id} not found")
    messages = load_history(conversation_id, limit=limit)
    return {"conversation_id": conversation_id, "messages": messages}


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(conversation_id: str, body: MessageRequest):
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conversation_id} not found")

    return StreamingResponse(
        stream_turn(
            conversation_id=conversation_id,
            user_id=body.user_id,
            user_message=body.message,
            tenant_id=body.tenant_id,
            is_first_turn=body.is_first_turn,
        ),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/users/{user_id}/memory")
def user_memory(user_id: str, tenant_id: str = "default", max_items: int = 5):
    items = load_user_memory(user_id, tenant_id, max_items)
    return {"user_id": user_id, "memory": items}
