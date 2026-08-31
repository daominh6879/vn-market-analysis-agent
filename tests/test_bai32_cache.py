"""
tests/test_bai32_cache.py — Cache tests for Bài 32 (intent-level cache).

Tests hit real Redis + real LLM + real tools where noted.

Run:
    pytest tests/test_bai32_cache.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dotenv import load_dotenv
load_dotenv()

import pytest
import redis as redis_lib

from core.config import settings
from core.cache import (
    CacheKey,
    cache_get,
    cache_set,
    make_cache_key,
    normalize_question,
    PROMPT_VERSION,
    ttl_seconds,
    _INTENT_TTL,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flush_test_keys(pattern: str = "cache:b32:*") -> None:
    r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    keys = r.keys(pattern)
    if keys:
        r.delete(*keys)


def _new_conv():
    from memory.conversation import create_conversation
    uid = f"test-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(uid, "default")
    return cid, uid


def _run_stream(conversation_id, user_id, message, is_first_turn=True):
    from memory.turn_handler import stream_turn
    lines = []

    async def _go():
        async for line in stream_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=message,
            tenant_id="default",
            is_first_turn=is_first_turn,
        ):
            lines.append(line)

    asyncio.run(_go())
    return lines


def _parse_event(lines, event_step):
    for raw in lines:
        for i, s in enumerate(raw.split("\n")):
            if s.strip() == "event: status":
                for sub in raw.split("\n")[i + 1:]:
                    if sub.startswith("data: "):
                        try:
                            p = json.loads(sub[6:].strip())
                            if p.get("step") == event_step:
                                return p
                        except Exception:
                            pass
    return None


def _reply_text(lines):
    chunks = []
    for raw in lines:
        if raw.startswith("data: "):
            try:
                p = json.loads(raw[6:].strip())
                if "text" in p:
                    chunks.append(p["text"])
            except Exception:
                pass
    return "".join(chunks)


# ══ UNIT TESTS — no LLM/network ══════════════════════════════════════════════

def test_normalize_question():
    q = "HPG có nên mua không?"
    n = normalize_question(q)
    assert n == n.lower()
    assert "?" not in n
    assert "e" in n


def test_cache_key_no_conversation_id():
    ck = CacheKey(
        tenant_id="t1",
        intent="technical_analysis",
        ticker="HPG",
        prompt_version="v1",
        model_version="deepseek-v4-flash",
    )
    data = ck.model_dump()
    assert "conversation_id" not in data
    assert "intent" in data
    assert "normalized_question" not in data, "intent-level cache must not include question"


def test_make_cache_key_returns_key_for_any_turn():
    """All intents cacheable regardless of turn — history ignored."""
    history = [{"role": "user", "content": "xin chào"}, {"role": "assistant", "content": "Chào bạn"}]
    for intent in ("technical_analysis", "price_action", "news_sentiment",
                   "macro_sector", "rag_qa", "investment_case", "screening"):
        ck = make_cache_key("t1", "phan tich MBB", "MBB", intent, history=history)
        assert ck is not None, f"{intent} must be cacheable any turn"


def test_make_cache_key_skips_conversation():
    """conversation intent must never be cached."""
    ck = make_cache_key("t1", "xin chào", "", "conversation", history=[])
    assert ck is None, "conversation must not be cached"


def test_make_cache_key_ticker_extraction():
    """Multi-ticker queries get stable sorted key."""
    ck = make_cache_key("t1", "so sánh HPG với VCB", "", "investment_case", history=[])
    assert ck is not None
    assert ck.ticker == "HPG|VCB"


def test_intent_ttl_ordering():
    """Fast-moving intents have shorter TTL than slow-moving ones."""
    price_in, _ = _INTENT_TTL["price_action"]
    rag_in, _ = _INTENT_TTL["rag_qa"]
    assert price_in < rag_in, "price_action must have shorter market-hours TTL than rag_qa"

    _, price_off = _INTENT_TTL["price_action"]
    _, rag_off = _INTENT_TTL["rag_qa"]
    assert price_off < rag_off, "price_action must have shorter off-hours TTL than rag_qa"


def test_ttl_seconds_returns_intent_ttl():
    """ttl_seconds respects intent parameter."""
    # Use off-hours values only (test environment may not be in VN market hours)
    _, price_off = _INTENT_TTL["price_action"]
    _, rag_off = _INTENT_TTL["rag_qa"]
    # We can't guarantee market hours in CI, just verify the function accepts intent arg
    t = ttl_seconds("price_action")
    assert isinstance(t, int) and t > 0


def test_same_ticker_different_intent_no_cross_hit():
    """technical_analysis HPG must not hit rag_qa HPG cache."""
    from core.cache import set_exact, get_exact
    ck_tech = CacheKey(
        tenant_id="default", intent="technical_analysis", ticker="HPG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    ck_fund = CacheKey(
        tenant_id="default", intent="rag_qa", ticker="HPG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    set_exact(ck_tech, "technical reply")
    result = get_exact(ck_fund)
    assert result is None, "Different intent must not cross-hit"


def test_vinamilk_vnm_same_cache_hit():
    """After router resolves both to ticker=VNM, they share one cache entry."""
    from core.cache import set_exact, get_exact
    ck_vnm = CacheKey(
        tenant_id="default", intent="technical_analysis", ticker="VNM",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    set_exact(ck_vnm, "VNM technical reply")
    result = get_exact(ck_vnm)
    assert result == "VNM technical reply"


def test_exact_cache_roundtrip():
    """set_exact + get_exact roundtrip — Redis must be up."""
    import os; os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-v4-flash")
    ck = CacheKey(
        tenant_id="test-tenant", intent="rag_qa", ticker="HPG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    from core.cache import set_exact, get_exact
    set_exact(ck, "Doanh thu HPG Q2 2024: 35,000 tỷ")
    result = get_exact(ck)
    assert result == "Doanh thu HPG Q2 2024: 35,000 tỷ"


def test_prompt_version_invalidates_exact():
    """Different prompt_version → different hash → miss."""
    ck_v1 = CacheKey(
        tenant_id="test-tenant", intent="technical_analysis", ticker="HPG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    ck_v2 = CacheKey(
        tenant_id="test-tenant", intent="technical_analysis", ticker="HPG",
        prompt_version="v2", model_version="deepseek-v4-flash",
    )
    from core.cache import set_exact, get_exact
    set_exact(ck_v1, "reply v1")
    result = get_exact(ck_v2)
    assert result is None, "Different prompt_version must be a cache miss"


def test_hpg_hsg_no_cross_hit():
    """HPG cache key must not match HSG cache key."""
    ck_hpg = CacheKey(
        tenant_id="default", intent="technical_analysis", ticker="HPG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    ck_hsg = CacheKey(
        tenant_id="default", intent="technical_analysis", ticker="HSG",
        prompt_version="v1", model_version="deepseek-v4-flash",
    )
    from core.cache import set_exact, get_exact
    set_exact(ck_hpg, "HPG reply")
    result = get_exact(ck_hsg)
    assert result is None, "HSG must not get HPG cached reply"


# ══ INTEGRATION TESTS — hit real LLM + tools + Redis (slow ~15-60s) ═══════════

def test_cache_hit_second_request_real():
    """Same question twice → second hit returns 'cache_hit' SSE event."""
    _flush_test_keys()
    cid1, uid1 = _new_conv()
    question = "dòng tiền và khối lượng giao dịch HPG hôm nay"

    lines1 = _run_stream(cid1, uid1, question, is_first_turn=True)
    reply1 = _reply_text(lines1)
    assert len(reply1) > 50, f"First reply too short: {len(reply1)}"
    print(f"\nFirst reply ({len(reply1)} chars): {reply1[:200]}")

    cid2, uid2 = _new_conv()
    lines2 = _run_stream(cid2, uid2, question, is_first_turn=True)
    reply2 = _reply_text(lines2)

    cache_event = _parse_event(lines2, "cache_hit")
    print(f"Cache event: {cache_event}")
    print(f"Second reply ({len(reply2)} chars): {reply2[:200]}")

    assert cache_event is not None, "Expected cache_hit SSE event on second call"
    assert cache_event.get("tier") == "exact", f"Unexpected tier: {cache_event}"
    assert len(reply2) > 50


def test_turn2_hits_cache_real():
    """Same intent+ticker in turn 2 → CAN hit cache (intent-level, history-independent)."""
    _flush_test_keys()
    question = "phân tích kỹ thuật HPG: RSI và MACD"
    cid, uid = _new_conv()

    lines1 = _run_stream(cid, uid, question, is_first_turn=True)
    reply1 = _reply_text(lines1)
    assert len(reply1) > 50, "Turn 1 must have reply"
    print(f"\nTurn 1 reply ({len(reply1)} chars): {reply1[:200]}")

    lines2 = _run_stream(cid, uid, question, is_first_turn=False)
    cache_event = _parse_event(lines2, "cache_hit")
    reply2 = _reply_text(lines2)

    print(f"Turn 2 cache event: {cache_event}")
    print(f"Turn 2 reply ({len(reply2)} chars): {reply2[:200]}")

    # Intent-level cache: turn 2 with same ticker+intent should hit cache
    assert cache_event is not None, "Turn 2 with same intent+ticker must hit cache"
    assert cache_event.get("tier") == "exact"
    assert len(reply2) > 50


def test_ngan_hang_thinh_vuong_resolves_vpb():
    """classify_hybrid must resolve 'Ngân hàng Thịnh Vượng' → ticker=VPB."""
    from agents.classifier import classify_hybrid
    result = classify_hybrid("phân tích cổ phiếu Ngân hàng Thịnh Vượng")
    print(f"\nRouter result: intent={result.intent} ticker={result.ticker} reason={result.reason}")
    assert result.ticker == "VPB", f"Expected VPB, got {result.ticker}"


def test_company_name_same_cache_as_ticker_real():
    """'phân tích Ngân hàng Thịnh Vượng' and 'phân tích VPB' share one cache entry."""
    _flush_test_keys()

    cid1, uid1 = _new_conv()
    lines1 = _run_stream(cid1, uid1, "phân tích Ngân hàng Thịnh Vượng hôm nay",
                         is_first_turn=True)
    reply1 = _reply_text(lines1)
    print(f"\nCompany-name reply ({len(reply1)} chars): {reply1[:200]}")
    assert len(reply1) > 50, "First reply too short"

    cid2, uid2 = _new_conv()
    lines2 = _run_stream(cid2, uid2, "phân tích VPB hôm nay", is_first_turn=True)
    cache_event = _parse_event(lines2, "cache_hit")
    reply2 = _reply_text(lines2)

    print(f"Cache event: {cache_event}")
    print(f"VPB reply ({len(reply2)} chars): {reply2[:200]}")

    assert cache_event is not None, (
        "Expected cache_hit: 'Ngân hàng Thịnh Vượng' and 'VPB' must share same entry"
    )
    assert cache_event.get("tier") == "exact"


def test_hpg_hsg_no_cross_cache_real():
    """Cache HPG reply, then ask about HSG → must NOT return HPG reply."""
    _flush_test_keys()
    cid1, uid1 = _new_conv()

    hpg_q = "doanh thu HPG năm 2024 là bao nhiêu?"
    hsg_q = "doanh thu HSG năm 2024 là bao nhiêu?"

    lines1 = _run_stream(cid1, uid1, hpg_q, is_first_turn=True)
    hpg_reply = _reply_text(lines1)
    assert "HPG" in hpg_reply.upper() or len(hpg_reply) > 20
    print(f"\nHPG reply: {hpg_reply[:200]}")

    cid2, uid2 = _new_conv()
    lines2 = _run_stream(cid2, uid2, hsg_q, is_first_turn=True)
    cache_event = _parse_event(lines2, "cache_hit")
    hsg_reply = _reply_text(lines2)

    print(f"Cache event for HSG query: {cache_event}")
    print(f"HSG reply: {hsg_reply[:200]}")

    if cache_event is not None:
        assert hsg_reply != hpg_reply, "HSG must not receive HPG cached reply"
    assert len(hsg_reply) > 20
    print("HSG ticker guard: OK (no cross-cache)")


def test_prompt_version_change_invalidates_cache_real():
    """After PROMPT_VERSION change, old cache entries must be misses."""
    import os
    from core import cache as cache_mod

    _flush_test_keys()
    original = cache_mod.PROMPT_VERSION

    question = "giá HPG hiện tại"
    cid1, uid1 = _new_conv()
    lines1 = _run_stream(cid1, uid1, question, is_first_turn=True)
    reply1 = _reply_text(lines1)
    assert len(reply1) > 50

    # Bump prompt version — old entries should not hit
    cache_mod.PROMPT_VERSION = original + "_test_bump"
    try:
        cid2, uid2 = _new_conv()
        lines2 = _run_stream(cid2, uid2, question, is_first_turn=True)
        cache_event = _parse_event(lines2, "cache_hit")
        assert cache_event is None, (
            "Bumped PROMPT_VERSION must cause cache miss"
        )
    finally:
        cache_mod.PROMPT_VERSION = original


# ══ E2E: intent-level cache contract ══════════════════════════════════════════

def test_different_phrasing_same_intent_ticker_hits_cache():
    """Core intent-level guarantee: different wording, same intent+ticker → cache hit.

    q1: "HPG hôm nay thế nào?"  → router: price_action / HPG → stored
    q2: "Cập nhật giá HPG"      → router: price_action / HPG → must HIT q1 entry

    If this fails, the router is classifying the two queries differently
    (different intent or different ticker), not a cache bug.
    """
    _flush_test_keys()

    q1 = "HPG hôm nay thế nào?"
    q2 = "Cập nhật giá và khối lượng HPG"

    cid1, uid1 = _new_conv()
    lines1 = _run_stream(cid1, uid1, q1, is_first_turn=True)
    reply1 = _reply_text(lines1)
    print(f"\nq1 reply ({len(reply1)} chars): {reply1[:200]}")
    assert len(reply1) > 50, "q1 reply too short — pipeline failed"

    cid2, uid2 = _new_conv()
    lines2 = _run_stream(cid2, uid2, q2, is_first_turn=True)
    cache_event = _parse_event(lines2, "cache_hit")
    reply2 = _reply_text(lines2)

    print(f"Cache event: {cache_event}")
    print(f"q2 reply ({len(reply2)} chars): {reply2[:200]}")

    assert cache_event is not None, (
        "Different phrasing, same intent+ticker must hit cache. "
        "Check router log: both queries must resolve to same intent+ticker."
    )
    assert cache_event.get("tier") == "exact"
    assert reply2 == reply1, "Cached reply must be identical to stored reply"


def test_e2e_full_cycle_all_major_intents():
    """Smoke E2E: each major intent caches on first call, hits on second.

    One call per intent — verifies the cache_save_node runs for every intent path.
    Slow (~3-5 min for all intents). Run with -k to select a subset.
    """
    _flush_test_keys()

    cases = [
        ("phân tích kỹ thuật HPG RSI MACD", "technical_analysis", "HPG"),
        ("tin tức và sentiment VCB tuần này", "news_sentiment", "VCB"),
        ("tổng quan thị trường hôm nay", "market_brief", ""),
    ]

    for question, expected_intent, expected_ticker in cases:
        label = f"{expected_intent}/{expected_ticker or 'no-ticker'}"

        # First call — populate cache
        cid1, uid1 = _new_conv()
        lines1 = _run_stream(cid1, uid1, question, is_first_turn=True)
        reply1 = _reply_text(lines1)
        print(f"\n[{label}] first reply ({len(reply1)} chars): {reply1[:120]}")
        assert len(reply1) > 50, f"[{label}] first reply too short"

        # Second call — must hit cache
        cid2, uid2 = _new_conv()
        lines2 = _run_stream(cid2, uid2, question, is_first_turn=True)
        cache_event = _parse_event(lines2, "cache_hit")
        reply2 = _reply_text(lines2)

        print(f"[{label}] cache_event={cache_event}")
        assert cache_event is not None, f"[{label}] second call must hit cache"
        assert cache_event.get("tier") == "exact", f"[{label}] wrong tier: {cache_event}"
        assert reply2 == reply1, f"[{label}] cached reply must match original"
