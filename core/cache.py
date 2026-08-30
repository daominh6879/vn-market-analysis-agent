"""
core/cache.py — 2-tier response cache for chat agent (Bài 32).

Tier 1 (exact): SHA-256(CacheKey JSON) → Redis  key: cache:b32:exact:{hash}
Tier 2 (vector): embed(normalized_question) → Qdrant 'cache_vectors'
                 Ticker guard: payload.ticker must match before returning.
                 Prevents HPG/HSG cross-cache contamination.

Rules:
- Only cache turn 1 (history empty). Turn 2+ → skip, always miss.
- No conversation_id in key — cache is cross-conversation.
- normalize: lowercase + NFKD diacritic strip + drop non-alphanumeric.
- TTL: 120s during market hours (Mon-Fri 09:00–14:45 VN), 1800s otherwise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

import redis as redis_lib
from pydantic import BaseModel

from core.config import settings

# ── Constants ─────────────────────────────────────────────────────────────────

_COLLECTION = "cache_vectors"
_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
_VECTOR_THRESHOLD = float(os.environ.get("CACHE_VECTOR_THRESHOLD", "0.92"))
_REDIS_PREFIX = "cache:b32:exact"
PROMPT_VERSION = os.environ.get("CACHE_PROMPT_VERSION", "v1")

_VN_TZ = timezone(timedelta(hours=7))


# Intents whose result depends on question wording (RAG-based) — include normalized_question in key.
# Pure-tool intents (same tools, same data for same ticker) — key on (intent, ticker) only.
_RAG_INTENTS = frozenset({"rag_qa", "screening"})

# Pure-tool intents: result is data-driven only, independent of conversation history.
# Cache these regardless of turn number — history context doesn't affect the output.
# RAG intents and conversation stay turn-1-only (history may affect the answer).
_HISTORY_INDEPENDENT = frozenset({
    "price_action",
    "technical_analysis",
    "news_sentiment",
    "macro_sector",
    "market_brief",
    "investment_case",  # tool-driven (financial statements), not RAG — history-independent
})

# ── Key model ─────────────────────────────────────────────────────────────────

class CacheKey(BaseModel):
    tenant_id: str
    intent: str          # from router — determines which tools/path run
    ticker: str          # uppercase; "" for ticker-less intents (screening, macro_sector)
    normalized_question: str  # "" for pure-tool intents; full question for RAG intents
    prompt_version: str
    model_version: str
    # NO conversation_id — cache is cross-conversation


def normalize_question(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, drop non-alphanumeric (keep spaces)."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() or c == " " else " " for c in ascii_only)
    return " ".join(cleaned.split())


def make_cache_key(
    tenant_id: str,
    question: str,
    ticker: str,
    intent: str,
    history: list,
) -> Optional[CacheKey]:
    """Return CacheKey, or None if this turn should not be cached.

    Pure-tool intents (_HISTORY_INDEPENDENT): cache any turn — result is data-driven,
    conversation history doesn't change the output.

    RAG intents (rag_qa, screening) and conversation: turn 1 only — history may affect answer.

    RAG intents include normalized_question in key (different questions → different RAG chunks).
    Pure-tool intents use normalized_question="" — same tools run regardless of phrasing.
    """
    # Pure-tool intents are history-independent — cache regardless of turn.
    # RAG/conversation intents: turn 1 only (history changes the answer).
    if history and intent not in _HISTORY_INDEPENDENT:
        return None
    model_version = os.environ.get("DEEPSEEK_MODEL", "unknown")
    nq = normalize_question(question) if intent in _RAG_INTENTS else ""
    return CacheKey(
        tenant_id=tenant_id,
        intent=intent,
        ticker=ticker.upper() if ticker else "",
        normalized_question=nq,
        prompt_version=PROMPT_VERSION,
        model_version=model_version,
    )


def _key_hash(ck: CacheKey) -> str:
    raw = json.dumps(ck.model_dump(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── TTL ───────────────────────────────────────────────────────────────────────

def ttl_seconds() -> int:
    """120s during VN market hours (Mon-Fri 09:00-14:45), 1800s otherwise."""
    now = datetime.now(_VN_TZ)
    if now.weekday() < 5:
        t = now.time()
        from datetime import time as time_cls
        if time_cls(9, 0) <= t <= time_cls(14, 45):
            return 120
    return 1800


# ── Redis client (lazy) ───────────────────────────────────────────────────────

_redis: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# ── Tier 1: exact (Redis) ─────────────────────────────────────────────────────

def get_exact(ck: CacheKey) -> Optional[str]:
    h = _key_hash(ck)
    try:
        r = _get_redis()
        val = r.get(f"{_REDIS_PREFIX}:{h}")
        if val is not None:
            log.debug("cache.exact.hit intent=%s ticker=%s hash=%s", ck.intent, ck.ticker, h[:12])
        else:
            log.debug("cache.exact.miss intent=%s ticker=%s hash=%s", ck.intent, ck.ticker, h[:12])
        return val
    except Exception as exc:
        log.warning("cache.exact.error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)
        return None


def set_exact(ck: CacheKey, reply: str) -> None:
    h = _key_hash(ck)
    ttl = ttl_seconds()
    try:
        r = _get_redis()
        r.set(f"{_REDIS_PREFIX}:{h}", reply, ex=ttl)
        log.debug("cache.exact.set intent=%s ticker=%s hash=%s ttl=%ds", ck.intent, ck.ticker, h[:12], ttl)
    except Exception as exc:
        log.warning("cache.exact.set_error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)


# ── Tier 2: vector (Qdrant) ───────────────────────────────────────────────────

def _qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)


def _embed(text: str) -> list[float]:
    import httpx
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    for path, payload in (
        ("/api/embed",      {"model": _EMBED_MODEL, "input": text}),
        ("/api/embeddings", {"model": _EMBED_MODEL, "prompt": text}),
    ):
        r = httpx.post(f"{ollama_url}{path}", json=payload, timeout=30)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        data = r.json()
        vec = data.get("embedding") or (data.get("embeddings") or [[]])[0]
        if vec:
            return vec
    raise RuntimeError("Ollama embed failed")


def _ensure_collection(dim: int) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.models import VectorParams, Distance
    client = _qdrant()
    existing = {c.name for c in client.get_collections().collections}
    if _COLLECTION not in existing:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )


def get_vector(ck: CacheKey) -> Optional[str]:
    # Vector tier only meaningful when there's a question to embed
    if not ck.normalized_question:
        log.debug("cache.vector.skip intent=%s ticker=%s reason=no_question", ck.intent, ck.ticker)
        return None
    try:
        vec = _embed(ck.normalized_question)
        client = _qdrant()
        results = client.search(
            collection_name=_COLLECTION,
            query_vector=vec,
            limit=5,
            score_threshold=_VECTOR_THRESHOLD,
            with_payload=True,
        )
        now = time.time()
        for r in results:
            p = r.payload or {}
            score = round(r.score, 4)
            # Ticker guard — must match exactly
            if p.get("ticker", "") != ck.ticker:
                log.debug("cache.vector.guard_fail reason=ticker expected=%s got=%s score=%.4f",
                          ck.ticker, p.get("ticker"), score)
                continue
            # Intent guard — rag_qa and screening can share same ticker+question text
            if p.get("intent", "") != ck.intent:
                log.debug("cache.vector.guard_fail reason=intent expected=%s got=%s score=%.4f",
                          ck.intent, p.get("intent"), score)
                continue
            # Tenant guard
            if p.get("tenant_id", "") != ck.tenant_id:
                log.debug("cache.vector.guard_fail reason=tenant score=%.4f", score)
                continue
            # Prompt/model version guard
            if p.get("prompt_version", "") != ck.prompt_version:
                log.debug("cache.vector.guard_fail reason=prompt_version score=%.4f", score)
                continue
            if p.get("model_version", "") != ck.model_version:
                log.debug("cache.vector.guard_fail reason=model_version score=%.4f", score)
                continue
            # TTL check
            if p.get("expires_at", 0) < now:
                log.debug("cache.vector.guard_fail reason=expired score=%.4f", score)
                continue
            log.debug("cache.vector.hit intent=%s ticker=%s score=%.4f", ck.intent, ck.ticker, score)
            return p.get("reply", "")
        log.debug("cache.vector.miss intent=%s ticker=%s candidates=%d", ck.intent, ck.ticker, len(results))
    except Exception as exc:
        log.warning("cache.vector.error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)
    return None


def set_vector(ck: CacheKey, reply: str) -> None:
    if not ck.normalized_question:
        return  # pure-tool intents: no question to embed, skip vector tier
    try:
        import uuid as uuid_lib
        from qdrant_client.models import PointStruct
        vec = _embed(ck.normalized_question)
        _ensure_collection(len(vec))
        client = _qdrant()
        h = _key_hash(ck)
        point_id = str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, h))
        ttl = ttl_seconds()
        expires_at = time.time() + ttl
        client.upsert(
            collection_name=_COLLECTION,
            points=[PointStruct(
                id=point_id,
                vector=vec,
                payload={
                    "intent": ck.intent,
                    "ticker": ck.ticker,
                    "tenant_id": ck.tenant_id,
                    "prompt_version": ck.prompt_version,
                    "model_version": ck.model_version,
                    "reply": reply,
                    "expires_at": expires_at,
                },
            )],
        )
        log.debug("cache.vector.set intent=%s ticker=%s ttl=%ds", ck.intent, ck.ticker, ttl)
    except Exception as exc:
        log.warning("cache.vector.set_error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)


# ── Maintenance ───────────────────────────────────────────────────────────────

def purge_expired_vectors(batch_size: int = 500) -> int:
    """Delete expired points from Qdrant cache collection. Returns count deleted.

    Qdrant has no native TTL — expired points accumulate and must be purged manually.
    Call periodically (e.g. via a Dagster sensor or cron).
    """
    try:
        from qdrant_client.models import Filter, FieldCondition, Range
        client = _qdrant()
        existing = {c.name for c in client.get_collections().collections}
        if _COLLECTION not in existing:
            return 0
        now = time.time()
        client.delete(
            collection_name=_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="expires_at", range=Range(lte=now))]
            ),
        )
        log.info("cache.vector.purge completed ts=%.0f", now)
        return 0  # Qdrant delete doesn't return count
    except Exception as exc:
        log.warning("cache.vector.purge_error err=%s", exc)
        return 0


# ── Public API ────────────────────────────────────────────────────────────────

def cache_get(ck: CacheKey) -> tuple[Optional[str], str]:
    """Returns (reply, tier) where tier in {'exact', 'vector', 'miss'}."""
    hit = get_exact(ck)
    if hit is not None:
        return hit, "exact"
    hit = get_vector(ck)
    if hit is not None:
        return hit, "vector"
    return None, "miss"


def cache_set(ck: CacheKey, reply: str) -> None:
    """Write exact tier synchronously; vector tier in background thread (Ollama embed is slow)."""
    import threading
    set_exact(ck, reply)
    if ck.normalized_question:
        threading.Thread(target=set_vector, args=(ck, reply), daemon=True).start()
