"""
tests/test_bai32_six_intents.py â€” End-to-end tests for 6-intent router.

Unit tests: classify() â†’ correct intent for each of the 6 nhÃ³m.
Integration tests: SSE stream hitting real LLM + tools, one per nhÃ³m.

Run fast (unit only):
  pytest tests/test_bai32_six_intents.py -v -k "classify"

Run all (slow, hits LLM + external APIs):
  pytest tests/test_bai32_six_intents.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dotenv import load_dotenv
load_dotenv()

import pytest

from agents.classifier import classify, RouterResult


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _run_stream(conversation_id: str, user_id: str, message: str):
    from memory.turn_handler import stream_turn
    lines: list[str] = []

    async def _go():
        async for line in stream_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            user_message=message,
            tenant_id="default",
            is_first_turn=True,
        ):
            lines.append(line)

    asyncio.run(_go())
    return lines


def _parse_routing(lines: list[str]) -> dict | None:
    for raw in lines:
        for i, s in enumerate(raw.split("\n")):
            if s.strip() == "event: status":
                for sub in raw.split("\n")[i + 1:]:
                    if sub.startswith("data: "):
                        try:
                            p = json.loads(sub[6:].strip())
                            if p.get("step") == "routing":
                                return p
                        except Exception:
                            pass
    return None


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


def _reply_text(lines: list[str]) -> str:
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


def _new_conv():
    from memory.conversation import create_conversation
    uid = f"test-{uuid.uuid4().hex[:8]}"
    cid = create_conversation(uid, "default")
    return cid, uid


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# UNIT TESTS â€” classify() only, no LLM/network
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# â”€â”€ NhÃ³m 1: price_action â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_price_action_foreign():
    r = classify("khá»‘i ngoáº¡i mua bÃ¡n rÃ²ng HPG hÃ´m nay tháº¿ nÃ o?")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_price_action_volume():
    r = classify("khá»‘i lÆ°á»£ng giao dá»‹ch HPG cÃ³ Ä‘á»™t biáº¿n khÃ´ng?")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"


def test_classify_price_action_dÃ²ng_tiá»n():
    r = classify("dÃ²ng tiá»n vÃ o HPG hÃ´m nay")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"


# â”€â”€ NhÃ³m 2: technical_analysis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_technical_rsi_macd():
    r = classify("RSI vÃ  MACD cá»§a FPT Ä‘ang nhÆ° tháº¿ nÃ o?")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "FPT"


def test_classify_technical_support():
    r = classify("vÃ¹ng há»— trá»£ khÃ¡ng cá»± cá»§a VNM á»Ÿ Ä‘Ã¢u?")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "VNM"


def test_classify_technical_trend():
    r = classify("xu hÆ°á»›ng ká»¹ thuáº­t HPG tuáº§n nÃ y")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"


def test_classify_ticker_alone_defaults_technical():
    r = classify("phÃ¢n tÃ­ch HPG")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


# â”€â”€ NhÃ³m 3: fundamentals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_fundamentals_pe():
    r = classify("P/E cá»§a HPG hiá»‡n táº¡i so vá»›i trung bÃ¬nh ngÃ nh tháº¿ nÃ o?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_fundamentals_revenue():
    r = classify("doanh thu HPG nÄƒm 2024 lÃ  bao nhiÃªu?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"


def test_classify_fundamentals_roe():
    # ROE is in _VN_NOISE so won't be ticker; but "roe" keyword hits _FUNDAMENTALS_KW
    r = classify("ROE cá»§a VCB nÄƒm ngoÃ¡i lÃ  bao nhiÃªu?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"


# â”€â”€ NhÃ³m 4: macro_sector â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_macro_fx():
    r = classify("tá»· giÃ¡ USD/VND hÃ´m nay áº£nh hÆ°á»Ÿng gÃ¬ Ä‘áº¿n FPT?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


def test_classify_macro_commodity_steel():
    r = classify("giÃ¡ thÃ©p HRC tháº¿ giá»›i tÄƒng áº£nh hÆ°á»Ÿng HPG tháº¿ nÃ o?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


def test_classify_macro_oil():
    r = classify("dáº§u Brent hÃ´m nay giÃ¡ bao nhiÃªu?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


# â”€â”€ NhÃ³m 5: news_sentiment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_news_ticker():
    r = classify("tin tá»©c vá» HPG trong 3 ngÃ y gáº§n nháº¥t")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_news_sentiment():
    r = classify("sentiment cá»§a cá»™ng Ä‘á»“ng vá» VNM nhÆ° tháº¿ nÃ o?")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"


def test_classify_news_diá»…n_Ä‘Ã n():
    r = classify("diá»…n Ä‘Ã n Ä‘ang nÃ³i gÃ¬ vá» HPG?")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"


# â”€â”€ NhÃ³m 6: screening â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_screening_top():
    r = classify("top 5 mÃ£ cÃ³ ROE cao nháº¥t trong DB")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


def test_classify_screening_filter():
    r = classify("lá»c cá»• phiáº¿u ngÃ nh chá»©ng khoÃ¡n Ä‘ang tÃ­ch lÅ©y")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


def test_classify_screening_find():
    r = classify("tÃ¬m cá»• phiáº¿u cÃ³ RSI < 40 vÃ  P/E < 10")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


# â”€â”€ NhÃ³m 6b: investment_case â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_investment_case_buy():
    r = classify("HPG cÃ³ nÃªn mua khÃ´ng?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_investment_case_recommendation():
    r = classify("khuyáº¿n nghá»‹ VCB lÃºc nÃ y: mua bÃ¡n hay náº¯m giá»¯?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "VCB"


def test_classify_investment_case_tong_ket():
    r = classify("tá»•ng káº¿t FPT â€” bull case vÃ  bear case")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "FPT"


def test_classify_investment_case_dang_mua():
    r = classify("MWG Ä‘Ã¡ng Ä‘áº§u tÆ° khÃ´ng?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "MWG"


def test_classify_investment_case_phan_tich_toan_dien():
    r = classify("phÃ¢n tÃ­ch toÃ n diá»‡n HPG")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


# â”€â”€ market_brief & conversation (existing, regression) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_classify_market_brief():
    r = classify("thá»‹ trÆ°á»ng chá»©ng khoÃ¡n hÃ´m nay tháº¿ nÃ o?")
    assert r.intent == "market_brief"


def test_classify_market_brief_vnindex():
    r = classify("VNINDEX Ä‘ang á»Ÿ má»©c nÃ o?")
    assert r.intent == "market_brief"


def test_classify_conversation():
    r = classify("xin chÃ o báº¡n tÃªn gÃ¬")
    assert r.intent == "conversation"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# INTEGRATION TESTS â€” hit real LLM + tools (slow, ~15-60s each)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def test_route_price_action_real():
    """NhÃ³m 1: dÃ²ng tiá»n query â†’ price_action â†’ realtime price + foreign flow."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "dÃ²ng tiá»n vÃ  khá»‘i lÆ°á»£ng giao dá»‹ch HPG hÃ´m nay")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Done: {done}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None, "routing event missing"
    assert routing.get("agent") == "price_action", f"wrong intent: {routing}"
    assert done is not None, "done event missing"
    assert len(reply) > 50, f"reply too short: {len(reply)}"


def test_route_technical_analysis_real():
    """NhÃ³m 2: ká»¹ thuáº­t query â†’ technical_analysis â†’ OHLCV + indicators."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "phÃ¢n tÃ­ch ká»¹ thuáº­t HPG: RSI, MACD, vÃ¹ng há»— trá»£ khÃ¡ng cá»±")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "technical_analysis", f"wrong intent: {routing}"
    assert done is not None
    assert len(reply) > 100, f"reply too short"


def test_route_fundamentals_real():
    """NhÃ³m 3: P/E, doanh thu query â†’ fundamentals â†’ rag/qa."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "doanh thu vÃ  lá»£i nhuáº­n HPG nÄƒm 2024 lÃ  bao nhiÃªu?")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "rag_qa", f"wrong intent: {routing}"
    assert done is not None
    assert len(reply) > 20


def test_route_macro_sector_real():
    """NhÃ³m 4: tá»· giÃ¡ query â†’ macro_sector â†’ FX + commodities."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "tá»· giÃ¡ USD/VND hÃ´m nay vÃ  giÃ¡ dáº§u thÃ´ áº£nh hÆ°á»Ÿng ra sao?")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "macro_sector", f"wrong intent: {routing}"
    assert done is not None
    assert len(reply) > 50


def test_route_news_sentiment_real():
    """NhÃ³m 5: tin tá»©c query â†’ news_sentiment â†’ news + sentiment."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "tin tá»©c vá» HPG trong 3 ngÃ y gáº§n nháº¥t")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "news_sentiment", f"wrong intent: {routing}"
    assert done is not None
    assert len(reply) > 50


def test_route_screening_real():
    """NhÃ³m 6: lá»c cá»• phiáº¿u query â†’ screening â†’ SQL/RAG."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "top 5 mÃ£ cÃ³ ROE cao nháº¥t trong database")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Reply: {reply[:300]}")

    assert routing is not None
    assert routing.get("agent") == "screening", f"wrong intent: {routing}"
    assert done is not None
    assert len(reply) > 20


def test_route_investment_case_real():
    """NhÃ³m 6b: investment case â†’ calls all 5 intents â†’ bull/bear/recommendation."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "HPG cÃ³ nÃªn mua khÃ´ng? Cho mÃ¬nh bull case vÃ  bear case")

    routing = _parse_routing(lines)
    done = _parse_done(lines)
    reply = _reply_text(lines)

    print(f"\nRouting: {routing}")
    print(f"Done: {done}")
    print(f"Reply (first 500):\n{reply[:500]}")
    print(f"Reply (last 500):\n{reply[-500:]}")

    assert routing is not None, "routing event missing"
    assert routing.get("agent") == "investment_case", f"wrong intent: {routing}"
    assert done is not None, "done event missing"
    assert len(reply) > 200, f"reply too short: {len(reply)}"
    # Must contain key sections from the spec
    reply_lower = reply.lower()
    assert any(kw in reply_lower for kw in ["bull", "bear", "luáº­n Ä‘iá»ƒm", "khuyáº¿n nghá»‹"]), \
        "missing bull/bear/recommendation section"
    assert any(kw in reply_lower for kw in ["mua", "bÃ¡n", "náº¯m giá»¯", "tÃ­ch lÅ©y"]), \
        "missing buy/sell/hold verdict"
