"""
tests/test_bai32_six_intents.py — End-to-end tests for 6-intent router.

Unit tests: classify() → correct intent for each of the 6 nhóm.
Integration tests: SSE stream hitting real LLM + tools, one per nhóm.

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

from agents.query_router import classify, RouterResult


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — classify() only, no LLM/network
# ═══════════════════════════════════════════════════════════════════════════════

# ── Nhóm 1: price_action ─────────────────────────────────────────────────────

def test_classify_price_action_foreign():
    r = classify("khối ngoại mua bán ròng HPG hôm nay thế nào?")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_price_action_volume():
    r = classify("khối lượng giao dịch HPG có đột biến không?")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"


def test_classify_price_action_dòng_tiền():
    r = classify("dòng tiền vào HPG hôm nay")
    assert r.intent == "price_action", f"got {r.intent}: {r.reason}"


# ── Nhóm 2: technical_analysis ───────────────────────────────────────────────

def test_classify_technical_rsi_macd():
    r = classify("RSI và MACD của FPT đang như thế nào?")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "FPT"


def test_classify_technical_support():
    r = classify("vùng hỗ trợ kháng cự của VNM ở đâu?")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "VNM"


def test_classify_technical_trend():
    r = classify("xu hướng kỹ thuật HPG tuần này")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"


def test_classify_ticker_alone_defaults_technical():
    r = classify("phân tích HPG")
    assert r.intent == "technical_analysis", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


# ── Nhóm 3: fundamentals ─────────────────────────────────────────────────────

def test_classify_fundamentals_pe():
    r = classify("P/E của HPG hiện tại so với trung bình ngành thế nào?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_fundamentals_revenue():
    r = classify("doanh thu HPG năm 2024 là bao nhiêu?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"


def test_classify_fundamentals_roe():
    # ROE is in _VN_NOISE so won't be ticker; but "roe" keyword hits _FUNDAMENTALS_KW
    r = classify("ROE của VCB năm ngoái là bao nhiêu?")
    assert r.intent == "rag_qa", f"got {r.intent}: {r.reason}"


# ── Nhóm 4: macro_sector ─────────────────────────────────────────────────────

def test_classify_macro_fx():
    r = classify("tỷ giá USD/VND hôm nay ảnh hưởng gì đến FPT?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


def test_classify_macro_commodity_steel():
    r = classify("giá thép HRC thế giới tăng ảnh hưởng HPG thế nào?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


def test_classify_macro_oil():
    r = classify("dầu Brent hôm nay giá bao nhiêu?")
    assert r.intent == "macro_sector", f"got {r.intent}: {r.reason}"


# ── Nhóm 5: news_sentiment ───────────────────────────────────────────────────

def test_classify_news_ticker():
    r = classify("tin tức về HPG trong 3 ngày gần nhất")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_news_sentiment():
    r = classify("sentiment của cộng đồng về VNM như thế nào?")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"


def test_classify_news_diễn_đàn():
    r = classify("diễn đàn đang nói gì về HPG?")
    assert r.intent == "news_sentiment", f"got {r.intent}: {r.reason}"


# ── Nhóm 6: screening ────────────────────────────────────────────────────────

def test_classify_screening_top():
    r = classify("top 5 mã có ROE cao nhất trong DB")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


def test_classify_screening_filter():
    r = classify("lọc cổ phiếu ngành chứng khoán đang tích lũy")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


def test_classify_screening_find():
    r = classify("tìm cổ phiếu có RSI < 40 và P/E < 10")
    assert r.intent == "screening", f"got {r.intent}: {r.reason}"


# ── Nhóm 6b: investment_case ─────────────────────────────────────────────────

def test_classify_investment_case_buy():
    r = classify("HPG có nên mua không?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


def test_classify_investment_case_recommendation():
    r = classify("khuyến nghị VCB lúc này: mua bán hay nắm giữ?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "VCB"


def test_classify_investment_case_tong_ket():
    r = classify("tổng kết FPT — bull case và bear case")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "FPT"


def test_classify_investment_case_dang_mua():
    r = classify("MWG đáng đầu tư không?")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "MWG"


def test_classify_investment_case_phan_tich_toan_dien():
    r = classify("phân tích toàn diện HPG")
    assert r.intent == "investment_case", f"got {r.intent}: {r.reason}"
    assert r.ticker == "HPG"


# ── market_brief & conversation (existing, regression) ───────────────────────

def test_classify_market_brief():
    r = classify("thị trường chứng khoán hôm nay thế nào?")
    assert r.intent == "market_brief"


def test_classify_market_brief_vnindex():
    r = classify("VNINDEX đang ở mức nào?")
    assert r.intent == "market_brief"


def test_classify_conversation():
    r = classify("xin chào bạn tên gì")
    assert r.intent == "conversation"


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — hit real LLM + tools (slow, ~15-60s each)
# ═══════════════════════════════════════════════════════════════════════════════

def test_route_price_action_real():
    """Nhóm 1: dòng tiền query → price_action → realtime price + foreign flow."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "dòng tiền và khối lượng giao dịch HPG hôm nay")

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
    """Nhóm 2: kỹ thuật query → technical_analysis → OHLCV + indicators."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "phân tích kỹ thuật HPG: RSI, MACD, vùng hỗ trợ kháng cự")

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
    """Nhóm 3: P/E, doanh thu query → fundamentals → rag/qa."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?")

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
    """Nhóm 4: tỷ giá query → macro_sector → FX + commodities."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "tỷ giá USD/VND hôm nay và giá dầu thô ảnh hưởng ra sao?")

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
    """Nhóm 5: tin tức query → news_sentiment → news + sentiment."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "tin tức về HPG trong 3 ngày gần nhất")

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
    """Nhóm 6: lọc cổ phiếu query → screening → SQL/RAG."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "top 5 mã có ROE cao nhất trong database")

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
    """Nhóm 6b: investment case → calls all 5 intents → bull/bear/recommendation."""
    cid, uid = _new_conv()
    lines = _run_stream(cid, uid, "HPG có nên mua không? Cho mình bull case và bear case")

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
    assert any(kw in reply_lower for kw in ["bull", "bear", "luận điểm", "khuyến nghị"]), \
        "missing bull/bear/recommendation section"
    assert any(kw in reply_lower for kw in ["mua", "bán", "nắm giữ", "tích lũy"]), \
        "missing buy/sell/hold verdict"
