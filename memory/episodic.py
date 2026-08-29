"""
memory/episodic.py — Episodic memory backed by Qdrant (Bài 29).

store_episode(conversation_id, user_id, first_question, summary, conclusion, feedback)
retrieve_similar(query, user_id, top_k=3) → list[dict]  (with decay-adjusted score)

Forgetting — 3 layers:
  1. Hard expiry: episodes older than EXPIRY_DAYS (90) are excluded.
  2. Decay: raw similarity score × exp(-days_old / DECAY_HALF_LIFE).
  3. Hard limit: at most MAX_EPISODES (3) returned.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

COLLECTION = "episodic_memory"
EXPIRY_DAYS = 90
DECAY_HALF_LIFE = 30  # days
MAX_EPISODES = 3

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
EMBED_DIM = 1024  # bge-m3 output dim


def _qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


def _embed(text: str) -> list[float]:
    for path in ("/api/embed", "/api/embeddings"):
        payload = {"model": EMBED_MODEL, "input": text} if path == "/api/embed" else {"model": EMBED_MODEL, "prompt": text}
        try:
            r = httpx.post(f"{OLLAMA_URL}{path}", json=payload, timeout=60)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            vec = data.get("embedding") or (data.get("embeddings") or [[]])[0]
            if vec:
                return vec
        except Exception:
            continue
    raise RuntimeError(f"Failed to embed with model={EMBED_MODEL}")


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )


def _days_old(created_at_iso: str) -> float:
    try:
        created = datetime.fromisoformat(created_at_iso)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - created
        return max(0.0, delta.total_seconds() / 86400)
    except Exception:
        return 0.0


def store_episode(
    conversation_id: str,
    user_id: str,
    first_question: str,
    summary: str,
    conclusion: str,
    feedback: Optional[str] = None,
) -> str:
    """Embed first_question+summary and store as a point in Qdrant. Returns point id."""
    client = _qdrant()
    _ensure_collection(client)

    embed_text = f"{first_question}\n{summary}"
    vector = _embed(embed_text)

    point_id = str(uuid.uuid4())
    payload = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "first_question": first_question,
        "summary": summary,
        "conclusion": conclusion,
        "feedback": feedback or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    client.upsert(
        collection_name=COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )
    return point_id


def delete_episode(conversation_id: str) -> int:
    """Delete all Qdrant points for this conversation_id. Returns count deleted."""
    client = _qdrant()
    _ensure_collection(client)

    # Scroll to find matching point ids
    point_ids = []
    offset = None
    while True:
        results, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="conversation_id", match=MatchValue(value=conversation_id))]
            ),
            limit=100,
            offset=offset,
            with_payload=False,
        )
        point_ids.extend(r.id for r in results)
        if offset is None:
            break

    if point_ids:
        client.delete(
            collection_name=COLLECTION,
            points_selector=point_ids,
        )
    return len(point_ids)


def retrieve_similar(
    query: str,
    user_id: str,
    top_k: int = MAX_EPISODES,
) -> list[dict]:
    """
    Retrieve top_k most relevant recent episodes for user.
    Applies decay and filters out episodes older than EXPIRY_DAYS.
    Returns list of dicts with keys: conversation_id, first_question, summary, conclusion, score.
    """
    client = _qdrant()
    _ensure_collection(client)

    query_vec = _embed(query)

    # Fetch more candidates before applying decay + expiry filter
    response = client.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        query_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        ),
        limit=top_k * 5,
        with_payload=True,
    )
    candidates = response.points

    now = datetime.now(timezone.utc)
    results = []
    for hit in candidates:
        p = hit.payload or {}
        days = _days_old(p.get("created_at", ""))
        if days > EXPIRY_DAYS:
            continue
        decay = math.exp(-days / DECAY_HALF_LIFE)
        adjusted_score = (hit.score or 0.0) * decay
        results.append({
            "conversation_id": p.get("conversation_id", ""),
            "first_question": p.get("first_question", ""),
            "summary": p.get("summary", ""),
            "conclusion": p.get("conclusion", ""),
            "feedback": p.get("feedback", ""),
            "created_at": p.get("created_at", ""),
            "days_old": round(days, 1),
            "score": round(adjusted_score, 4),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
