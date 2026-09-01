"""
core/cache.py — Intent-level response cache (Bài 32).

Single tier: SHA-256(CacheKey JSON) → Redis  key: cache:b32:{hash}

Key = (tenant_id, intent, ticker, scope, prompt_version, model_version).
ticker fully differentiates most intents. For macro_sector/screening/rag_qa/breakout_scan
with empty ticker, scope = sha256(normalize_question)[:8] prevents cross-topic hits
(e.g. "xây dựng" and "ngân hàng" are both macro_sector+ticker="" but different scope).

TTL is per-intent, with separate market-hours and off-hours values:
  - Fast-moving data (price_action, breakout_scan): short TTL during market hours
  - Slow-moving data (rag_qa, investment_case): long TTL, cached overnight

Rules:
- conversation intent → never cached (no agent result to cache)
- All other intents → cached regardless of turn or conversation history
- No conversation_id in key — cache is cross-conversation
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re as _re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

import redis as redis_lib
from pydantic import BaseModel

from core.config import settings

# ── Constants ─────────────────────────────────────────────────────────────────

_REDIS_PREFIX = "cache:b32"
PROMPT_VERSION = os.environ.get("CACHE_PROMPT_VERSION", "v1")

_VN_TZ = timezone(timedelta(hours=7))

# Per-intent TTL: (market_hours_seconds, off_hours_seconds)
# Market hours: Mon-Fri 09:00-14:45 VN time
_INTENT_TTL: dict[str, tuple[int, int]] = {
    "price_action":       (60,    300),    # price ticks fast
    "technical_analysis": (300,   1800),   # indicators per-session
    "news_sentiment":     (600,   3600),   # news refreshes hourly
    "macro_sector":       (600,   3600),   # sector data slow-moving
    "market_brief":       (120,   1800),   # session overview
    "investment_case":    (1800,  86400),  # analysis stable intraday
    "rag_qa":             (3600,  86400),  # financial reports rarely change
    "screening":          (300,   3600),   # screen per-session
    "breakout_scan":      (120,   3600),   # breakout patterns time-sensitive
}
_DEFAULT_TTL = (300, 1800)

# ── Key model ─────────────────────────────────────────────────────────────────

class CacheKey(BaseModel):
    tenant_id: str
    intent: str
    ticker: str          # uppercase; "" for ticker-less intents
    scope: str           # "" for intents fully specified by ticker; question-hash otherwise
    prompt_version: str
    model_version: str
    # NO conversation_id — cache is cross-conversation, intent-level
    # scope differentiates same-intent queries when ticker is empty (macro_sector, screening, rag_qa)


def normalize_question(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, drop non-alphanumeric (keep spaces).
    Kept for compatibility — not used in cache key."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() or c == " " else " " for c in ascii_only)
    return " ".join(cleaned.split())


_TICKER_RE = _re.compile(r'\b([A-Z]{2,5})\b')
_TICKER_STOPWORDS = frozenset({"VE", "VA", "LA", "CO", "DE", "VS", "ROE", "ROA", "EPS", "PE", "PB"})


def _extract_all_tickers(question: str) -> str:
    """Extract all VN ticker mentions from query, sort + join for stable cache key.

    "HPG so với VCB" → "HPG|VCB" (same regardless of word order).
    Single ticker or none → unchanged.
    """
    hits = sorted(t for t in set(_TICKER_RE.findall(question.upper()))
                  if t not in _TICKER_STOPWORDS)
    return "|".join(hits) if len(hits) > 1 else (hits[0] if hits else "")


# Intents where ticker="" is ambiguous — need question-level scope to avoid cross-sector hits
_SCOPE_REQUIRED_INTENTS = frozenset({"macro_sector", "screening", "rag_qa", "breakout_scan"})


def _question_scope(question: str) -> str:
    """8-char hash of normalized question — stable scope for ticker-less intents."""
    normalized = normalize_question(question)
    return hashlib.sha256(normalized.encode()).hexdigest()[:8]


def make_cache_key(
    tenant_id: str,
    question: str,
    ticker: str,
    intent: str,
) -> Optional[CacheKey]:
    """Return CacheKey, or None if this turn should not be cached.

    conversation intent → always skip.
    Intents in _SCOPE_REQUIRED_INTENTS with empty ticker → scope = question hash
      (prevents macro_sector:banking and macro_sector:construction sharing one slot).
    All other intents → scope = "" (ticker fully differentiates).
    """
    if not intent or intent == "conversation":
        return None
    model_version = os.environ.get("DEEPSEEK_MODEL", "unknown")
    stable_ticker = _extract_all_tickers(question) or (ticker.upper() if ticker else "")
    scope = (
        _question_scope(question)
        if intent in _SCOPE_REQUIRED_INTENTS and not stable_ticker
        else ""
    )
    return CacheKey(
        tenant_id=tenant_id,
        intent=intent,
        ticker=stable_ticker,
        scope=scope,
        prompt_version=PROMPT_VERSION,
        model_version=model_version,
    )


def _key_hash(ck: CacheKey) -> str:
    raw = json.dumps(ck.model_dump(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── TTL ───────────────────────────────────────────────────────────────────────

def ttl_seconds(intent: str = "") -> int:
    """Return TTL for this intent based on whether VN market is open."""
    in_market, off_market = _INTENT_TTL.get(intent, _DEFAULT_TTL)
    now = datetime.now(_VN_TZ)
    if now.weekday() < 5:
        from datetime import time as _time
        if _time(9, 0) <= now.time() <= _time(14, 45):
            return in_market
    return off_market


# ── Redis client (lazy) ───────────────────────────────────────────────────────

_redis: Optional[redis_lib.Redis] = None


def _get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# ── Cache operations ──────────────────────────────────────────────────────────

def get_exact(ck: CacheKey) -> Optional[str]:
    h = _key_hash(ck)
    try:
        val = _get_redis().get(f"{_REDIS_PREFIX}:{h}")
        if val is not None:
            log.debug("cache.hit intent=%s ticker=%s hash=%s", ck.intent, ck.ticker, h[:12])
        else:
            log.debug("cache.miss intent=%s ticker=%s hash=%s", ck.intent, ck.ticker, h[:12])
        return val
    except Exception as exc:
        log.warning("cache.get_error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)
        return None


def set_exact(ck: CacheKey, reply: str) -> None:
    h = _key_hash(ck)
    ttl = ttl_seconds(ck.intent)
    try:
        _get_redis().set(f"{_REDIS_PREFIX}:{h}", reply, ex=ttl)
        log.debug("cache.set intent=%s ticker=%s hash=%s ttl=%ds", ck.intent, ck.ticker, h[:12], ttl)
    except Exception as exc:
        log.warning("cache.set_error intent=%s ticker=%s err=%s", ck.intent, ck.ticker, exc)


# ── Public API ────────────────────────────────────────────────────────────────

def cache_get(ck: CacheKey) -> tuple[Optional[str], str]:
    """Returns (reply, tier) where tier in {'exact', 'miss'}."""
    hit = get_exact(ck)
    if hit is not None:
        return hit, "exact"
    return None, "miss"


def cache_set(ck: CacheKey, reply: str) -> None:
    set_exact(ck, reply)
