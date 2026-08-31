"""
tests/test_bai31_routing.py â€” End-to-end routing tests hitting real LLM + tools.

Tests each intent route:
  ticker_analysis â†’ agents/graph.py (price data + technical indicators)
  market_brief    â†’ agents/market_brief_graph.py (global + VN market)
  qa_document     â†’ rag/qa.py (SQL / RAG)
  conversation    â†’ LLM stream direct

Run: pytest tests/test_bai31_routing.py -v -s
     (slow: ticker + market agents take 20-60s each)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dotenv import load_dotenv
load_dotenv()

import pytest

from agents.classifier import classify, RouterResult
from memory.conversation import create_conversation, load_history
from memory.turn_handler import stream_turn


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _run(conversation_id: str, user_id: str, message: str, is_first_turn: bool = False):
    """Run stream_turn, collect all SSE lines."""
    lines: list[str] = []

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


def _parse_done(lines: list[str]) -> dict | None:
    for raw in lines:
        for i, s in enumerate(raw.split("\n")):
            if s.strip() == "event: done":
                for sub in raw.split("\n")[i + 1:]:
                    if sub.startswith("data: "):
                        try:
                            return json.loads(sub[6:].strip())
                        except Exception:
                            pass
    return None


def _parse_routing_event(lines: list[str]) -> dict | None:
    """Find the routing status event and return its payload."""
    for raw in lines:
        subs = raw.split("\n")
        for i, s in enumerate(subs):
            if s.strip() == "event: status":
                for j in range(i + 1, len(subs)):
                    if subs[j].startswith("data: "):
                        try:
                            payload = json.loads(subs[j][6:].strip())
                            if payload.get("step") == "routing":
                                return payload
                        except Exception:
                            pass
    return None


def _chunks(lines: list[str]) -> list[str]:
    chunks = []
    for raw in lines:
        if raw.startswith("data: "):
            try:
                payload = json.loads(raw[6:].strip())
                if "text" in payload:
                    chunks.append(payload["text"])
            except Exception:
                pass
    return chunks


def _new_conv():
    uid = f"test-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(uid, "default")
    return cid, uid


# â”€â”€ Unit: classify() â€” no LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_ticker_analysis():
    r = classify("phÃ¢n tÃ­ch HPG hÃ´m nay")
    assert r.intent == "technical_analysis"
    assert r.ticker == "HPG"


def test_classify_market_brief():
    r = classify("thá»‹ trÆ°á»ng chá»©ng khoÃ¡n hÃ´m nay tháº¿ nÃ o?")
    assert r.intent == "market_brief"
    assert r.ticker is None


def test_classify_market_brief_vnindex():
    r = classify("VNINDEX Ä‘ang á»Ÿ Ä‘Ã¢u?")
    assert r.intent == "market_brief"


def test_classify_qa_document():
    r = classify("doanh thu HPG nÄƒm 2024 lÃ  bao nhiÃªu?")
    assert r.intent == "rag_qa"
    assert r.ticker == "HPG"


def test_classify_qa_document_sql():
    r = classify("top 5 mÃ£ cÃ³ ROE cao nháº¥t trong DB")
    assert r.intent == "screening"  # qa_document split â†’ screening


def test_classify_conversation():
    r = classify("xin chÃ o báº¡n tÃªn gÃ¬")
    assert r.intent == "conversation"


def test_classify_fpt_analysis():
    r = classify("chá»‰ sá»‘ ká»¹ thuáº­t cá»§a FPT tuáº§n nÃ y")
    assert r.intent == "technical_analysis"
    assert r.ticker == "FPT"


def test_classify_vnm():
    r = classify("phÃ¢n tÃ­ch VNM hÃ´m nay")
    assert r.intent == "technical_analysis"
    assert r.ticker == "VNM"


# â”€â”€ Integration: ticker_analysis hits real price API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_route_ticker_analysis_real():
    """HPG analysis query â†’ technical_analysis agent â†’ real price data + technical report."""
    cid, uid = _new_conv()
    lines = _run(cid, uid, "phÃ¢n tÃ­ch ká»¹ thuáº­t HPG hÃ´m nay", is_first_turn=True)

    routing = _parse_routing_event(lines)
    done = _parse_done(lines)
    chunks = _chunks(lines)
    reply = "".join(chunks)

    print(f"\nRouting: {routing}")
    print(f"Done: {done}")
    print(f"Reply preview: {reply[:300]}")

    assert routing is not None, "routing event missing"
    assert routing.get("agent") == "technical_analysis", f"expected technical_analysis, got {routing}"
    assert done is not None, "done event missing"
    assert done.get("agent") == "technical_analysis"
    assert done.get("saved") is True
    assert len(reply) > 100, f"report too short: {len(reply)} chars"

    # Verify it saved to history
    history = load_history(cid, limit=10)
    assert len(history) >= 2


def test_route_conversation_real():
    """Casual question â†’ conversation path â†’ direct LLM stream."""
    cid, uid = _new_conv()
    lines = _run(cid, uid, "xin chÃ o, báº¡n lÃ  ai?")

    routing = _parse_routing_event(lines)
    done = _parse_done(lines)
    reply = "".join(_chunks(lines))

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:200]}")

    assert routing is not None
    assert routing.get("agent") == "conversation"
    assert done is not None
    assert done.get("agent") == "conversation"
    assert len(reply) > 10


def test_route_qa_document_real():
    """Financial doc question â†’ qa_document â†’ SQL or RAG path."""
    cid, uid = _new_conv()
    lines = _run(cid, uid, "doanh thu HPG nÄƒm 2024 lÃ  bao nhiÃªu?")

    routing = _parse_routing_event(lines)
    done = _parse_done(lines)
    reply = "".join(_chunks(lines))

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "qa_document"
    assert done is not None
    assert len(reply) > 20


# â”€â”€ Market brief is slow (~60s) â€” run separately â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.slow
def test_route_market_brief_real():
    """Market query â†’ market_brief agent â†’ VN-Index + global data report."""
    cid, uid = _new_conv()
    lines = _run(cid, uid, "thá»‹ trÆ°á»ng chá»©ng khoÃ¡n Viá»‡t Nam hÃ´m nay tháº¿ nÃ o?",
                 is_first_turn=True)

    routing = _parse_routing_event(lines)
    done = _parse_done(lines)
    reply = "".join(_chunks(lines))

    print(f"\nRouting: {routing}")
    print(f"Done: {done}")
    print(f"Reply preview: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "market_brief"
    assert done is not None
    assert len(reply) > 100
