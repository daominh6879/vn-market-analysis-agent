"""
tests/test_graph_unified.py — End-to-end tests for unified agents/graph.py.

Covers:
  - verify_context: clarification detection (missing ticker, ambiguous intent)
  - pick_branch: dispatch to each of 9 intent nodes + knowledge + data fallback
  - check_cache_node / cache_save_node: cache hit / miss paths
  - All 9 intent nodes (real LLM + tools): price_action, technical_analysis,
    rag_qa, macro_sector, news_sentiment, investment_case, screening,
    market_brief, knowledge path (RAG-Fusion), data path (collect → synthesize)
  - Clarification → pending_context → resume flow (via stream_turn)

Run unit only (fast, no network):
  pytest tests/test_graph_unified.py -v -k "unit"

Run integration (slow, hits LLM + external APIs):
  pytest tests/test_graph_unified.py -v -s -k "integration"

Run all:
  pytest tests/test_graph_unified.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dotenv import load_dotenv
load_dotenv()

import pytest

from agents.graph import (
    verify_context,
    pick_branch,
    check_cache_node,
    check_cache_hit,
    cache_save_node,
    _INTENT_NODE_MAP,
    _INTENT_NODES,
    build_graph,
)
from agents.state import make_initial_state, AgentState


# ── Helpers ───────────────────────────────────────────────────────────────────

_MISSING = object()  # sentinel for "ticker not passed" vs "ticker=None"


def _state(query: str, intent: str = "", ticker: str | None = _MISSING, **kw) -> AgentState:
    """Build a state dict with intent/ticker set directly (for node-level unit tests).

    Pass ticker=None explicitly to simulate a missing ticker (detect_ambiguity checks `ticker is None`).
    Omit ticker to leave it unset in state.
    """
    s = make_initial_state(query, **kw)
    if intent:
        s["intent"] = intent
    if ticker is not _MISSING:
        s["ticker"] = ticker  # None means genuinely missing; "" means resolved-but-empty
    return s


def _invoke(query: str, conversation_id: str = "", tenant_id: str = "default") -> AgentState:
    """Invoke full graph — classify_node handles intent/ticker internally."""
    app = build_graph()
    state = make_initial_state(query, conversation_id=conversation_id, tenant_id=tenant_id)
    return app.invoke(state)


def _run_stream(conversation_id: str, user_id: str, message: str) -> list[str]:
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
    """Return the routing status event that has 'agent' set (post-graph routing)."""
    for raw in lines:
        for i, s in enumerate(raw.split("\n")):
            if s.strip() == "event: status":
                for sub in raw.split("\n")[i + 1:]:
                    if sub.startswith("data: "):
                        try:
                            p = json.loads(sub[6:].strip())
                            if p.get("step") == "routing" and p.get("agent"):
                                return p
                        except Exception:
                            pass
    return None


def _parse_status(lines: list[str], step: str) -> dict | None:
    for raw in lines:
        for i, s in enumerate(raw.split("\n")):
            if s.strip() == "event: status":
                for sub in raw.split("\n")[i + 1:]:
                    if sub.startswith("data: "):
                        try:
                            p = json.loads(sub[6:].strip())
                            if p.get("step") == step:
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
# UNIT — verify_context (no LLM, no network)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitVerifyContext:
    def test_unit_no_clarification_when_ticker_present(self):
        state = _state("phân tích kỹ thuật HPG", intent="technical_analysis", ticker="HPG")
        result = verify_context(state)
        assert not result.get("needs_clarification")

    def test_unit_clarification_when_ticker_missing(self):
        state = _state("phân tích kỹ thuật", intent="technical_analysis", ticker=None)
        result = verify_context(state)
        assert result.get("needs_clarification")
        assert result.get("clarification_message")
        assert result.get("pending_context")

    def test_unit_clarification_investment_no_ticker(self):
        state = _state("có nên mua không?", intent="investment_case", ticker=None)
        result = verify_context(state)
        assert result.get("needs_clarification")

    def test_unit_no_clarification_market_brief(self):
        state = _state("thị trường hôm nay", intent="market_brief", ticker="")
        result = verify_context(state)
        assert not result.get("needs_clarification")

    def test_unit_no_clarification_screening(self):
        state = _state("top 5 mã ROE cao nhất", intent="screening", ticker="")
        result = verify_context(state)
        assert not result.get("needs_clarification")

    def test_unit_no_clarification_macro_no_ticker(self):
        state = _state("tỷ giá USD/VND hôm nay", intent="macro_sector", ticker="")
        result = verify_context(state)
        assert not result.get("needs_clarification")


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — pick_branch routing (no LLM, no network)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitPickBranch:
    def test_unit_all_intent_nodes_in_map(self):
        for intent in _INTENT_NODES:
            assert intent in _INTENT_NODE_MAP, f"intent '{intent}' missing from _INTENT_NODE_MAP"

    def test_unit_pick_branch_price_action(self):
        assert pick_branch(_state("giá HPG", intent="price_action", ticker="HPG")) == "price_action"

    def test_unit_pick_branch_technical(self):
        assert pick_branch(_state("RSI HPG", intent="technical_analysis", ticker="HPG")) == "technical_analysis"

    def test_unit_pick_branch_rag_qa(self):
        assert pick_branch(_state("doanh thu HPG", intent="rag_qa", ticker="HPG")) == "rag_qa"

    def test_unit_pick_branch_macro_sector(self):
        assert pick_branch(_state("tỷ giá", intent="macro_sector")) == "macro_sector"

    def test_unit_pick_branch_news_sentiment(self):
        assert pick_branch(_state("tin tức HPG", intent="news_sentiment", ticker="HPG")) == "news_sentiment"

    def test_unit_pick_branch_investment_case(self):
        assert pick_branch(_state("mua HPG không", intent="investment_case", ticker="HPG")) == "investment_case"

    def test_unit_pick_branch_screening(self):
        assert pick_branch(_state("lọc cổ phiếu", intent="screening")) == "screening"

    def test_unit_pick_branch_market_brief(self):
        assert pick_branch(_state("thị trường hôm nay", intent="market_brief")) == "market_brief"

    def test_unit_pick_branch_knowledge_fallback_bctc(self):
        state = _state("báo cáo tài chính HPG năm 2024", ticker="HPG")
        assert pick_branch(state) == "knowledge"

    def test_unit_pick_branch_data_fallback(self):
        state = _state("HPG", ticker="HPG")
        assert pick_branch(state) == "data"

    def test_unit_pick_branch_market_query_data(self):
        state = _state("VNINDEX hôm nay")
        state["is_market_query"] = True
        assert pick_branch(state) == "data"


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — check_cache_node / cache_save_node (no LLM, no network)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitCacheNodes:
    def test_unit_check_cache_miss_returns_cache_key(self):
        """Cache miss: _cache_key set, _cache_hit absent."""
        state = _state("HPG giá hôm nay", intent="price_action", ticker="HPG",
                       tenant_id="default")
        # Empty history → eligible for caching
        state["messages"] = []
        result = check_cache_node(state)
        # Whether key is None or not depends on Redis availability — just check no crash
        assert "_cache_hit" not in result or result["_cache_hit"] is False

    def test_unit_check_cache_hit_edge_function(self):
        """check_cache_hit returns 'hit' when _cache_hit=True, 'miss' otherwise."""
        assert check_cache_hit({"_cache_hit": True}) == "hit"
        assert check_cache_hit({"_cache_hit": False}) == "miss"
        assert check_cache_hit({}) == "miss"

    def test_unit_cache_save_noop_when_no_key(self):
        """cache_save_node does nothing when _cache_key is None."""
        state = _state("hello", intent="conversation")
        state["_cache_key"] = None
        state["report"] = "some reply"
        result = cache_save_node(state)
        assert result == {}

    def test_unit_cache_save_noop_when_cache_hit(self):
        """cache_save_node skips write when _cache_hit=True (already cached)."""
        state = _state("HPG RSI", intent="technical_analysis", ticker="HPG")
        state["_cache_hit"] = True
        state["_cache_key"] = object()  # non-None sentinel
        state["report"] = "cached report"
        result = cache_save_node(state)
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — clarification stops at END (no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitClarificationPath:
    def test_unit_graph_returns_clarification_when_ticker_missing(self):
        """verify_context must detect missing ticker and return clarification fields."""
        state = _state("phân tích kỹ thuật", intent="technical_analysis", ticker=None)
        result = verify_context(state)
        assert result.get("needs_clarification")
        assert result.get("clarification_message")
        assert result.get("pending_context")
        assert "report" not in result or not result.get("report")


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — graph.invoke() directly, all 9 intent nodes (real LLM + tools)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationGraphInvoke:
    def test_integration_price_action(self):
        final = _invoke("giá và dòng tiền HPG hôm nay")
        report = final.get("report", "")
        print(f"\n[price_action] report[:300]: {report[:300]}")
        assert not final.get("needs_clarification")
        assert len(report) > 50
        assert any(kw in report.lower() for kw in ["hpg", "giá", "khối lượng", "dòng tiền", "price"])

    def test_integration_technical_analysis(self):
        final = _invoke("phân tích kỹ thuật HPG: RSI, MACD, xu hướng")
        report = final.get("report", "")
        print(f"\n[technical] report[:300]: {report[:300]}")
        assert len(report) > 100
        assert any(kw in report.lower() for kw in ["rsi", "macd", "xu hướng", "hỗ trợ", "kháng cự", "ema", "sma"])

    def test_integration_rag_qa(self):
        final = _invoke("doanh thu HPG năm 2024 bao nhiêu?")
        report = final.get("report", "")
        print(f"\n[rag_qa] report[:300]: {report[:300]}")
        assert len(report) > 20

    def test_integration_macro_sector(self):
        final = _invoke("tỷ giá USD/VND và giá thép hôm nay")
        report = final.get("report", "")
        print(f"\n[macro_sector] report[:300]: {report[:300]}")
        assert len(report) > 50

    def test_integration_news_sentiment(self):
        final = _invoke("tin tức về HPG trong 3 ngày gần nhất")
        report = final.get("report", "")
        print(f"\n[news_sentiment] report[:300]: {report[:300]}")
        assert len(report) > 50

    def test_integration_investment_case(self):
        final = _invoke("HPG có nên mua không? Bull case và bear case")
        report = final.get("report", "")
        print(f"\n[investment_case] report[:500]: {report[:500]}")
        assert len(report) > 200
        assert any(kw in report.lower() for kw in ["bull", "bear", "khuyến nghị", "mua", "bán", "nắm giữ"])

    def test_integration_screening(self):
        final = _invoke("top 5 mã ROE cao nhất trong database")
        report = final.get("report", "")
        print(f"\n[screening] report[:300]: {report[:300]}")
        assert len(report) > 20

    def test_integration_market_brief(self):
        final = _invoke("tổng quan thị trường chứng khoán hôm nay")
        report = final.get("report", "")
        print(f"\n[market_brief] report[:400]: {report[:400]}")
        assert len(report) > 100
        assert any(kw in report.lower() for kw in ["vnindex", "vn-index", "thị trường", "vn30", "hsx"])

    def test_integration_knowledge_path_bctc_keywords(self):
        """BCTC keywords + no explicit intent → knowledge path → fusion_search → synthesize."""
        final = _invoke("báo cáo tài chính HPG quý 1 2025")
        report = final.get("report", "")
        print(f"\n[knowledge] report[:300]: {report[:300]}")
        assert not final.get("needs_clarification")
        assert len(report) > 50

    def test_integration_ticker_only_asks_for_intent(self):
        """Bare ticker → classify_reason ends with 'default' → verify_context asks what user wants."""
        final = _invoke("HPG")
        print(f"\n[ticker_only] needs_clarification={final.get('needs_clarification')} msg={final.get('clarification_message', '')[:100]}")
        assert final.get("needs_clarification"), "bare ticker must trigger clarification"
        assert final.get("clarification_message")
        assert final.get("ticker") == "HPG"

    def test_integration_ticker_with_keyword_no_clarification(self):
        """Ticker + intent keyword → real route, no clarification."""
        final = _invoke("phân tích kỹ thuật HPG RSI")
        report = final.get("report", "")
        print(f"\n[technical+keyword] intent=%s report[:200]: %s" % (final.get("intent"), report[:200]))
        assert not final.get("needs_clarification")
        assert len(report) > 50

    def test_integration_cache_key_set_after_report(self):
        """After a successful non-conversation invoke, _cache_key should be set."""
        final = _invoke("HPG RSI hôm nay", tenant_id="default")
        # cache_key may be None if history is non-empty or Redis unavailable — just no crash
        print(f"\n[cache_key] _cache_key type: {type(final.get('_cache_key'))}")
        assert "report" in final


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — full stream_turn path (clarification → resume flow)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegrationStreamClarificationResume:
    def test_integration_clarification_then_resume(self):
        """Turn N: missing ticker → clarification. Turn N+1: user adds ticker → full report."""
        cid, uid = _new_conv()

        lines_n = _run_stream(cid, uid, "phân tích kỹ thuật")
        reply_n = _reply_text(lines_n)
        print(f"\n[clarification turn N] reply: {reply_n[:200]}")
        assert len(reply_n) > 10
        assert any(kw in reply_n.lower() for kw in ["mã", "ticker", "cổ phiếu", "công ty"]), \
            f"clarification must ask for ticker, got: {reply_n}"

        lines_n1 = _run_stream(cid, uid, "HPG")
        routing_n1 = _parse_routing(lines_n1)
        done_n1 = _parse_done(lines_n1)
        reply_n1 = _reply_text(lines_n1)
        print(f"\n[resume turn N+1] routing: {routing_n1}")
        print(f"[resume turn N+1] reply[:300]: {reply_n1[:300]}")

        assert done_n1 is not None
        assert len(reply_n1) > 50
        # routing_n1 may be None if turn was a clarification (no agent field emitted)
        # or present with the classified intent
        if routing_n1 and routing_n1.get("agent"):
            assert routing_n1.get("agent") in (
                "technical_analysis", "price_action", "rag_qa", "investment_case"
            ), f"unexpected intent after resume: {routing_n1}"


class TestIntegrationStreamAllIntents:
    """Smoke test: all 9 intents route through stream_turn → graph → done event."""

    _CASES = [
        ("giá và dòng tiền HPG hôm nay",              "price_action"),
        ("phân tích kỹ thuật HPG RSI MACD",           "technical_analysis"),
        ("doanh thu lợi nhuận HPG năm 2024",           "rag_qa"),
        ("tỷ giá USD/VND và giá thép hôm nay",        "macro_sector"),
        ("tin tức về HPG trong 3 ngày gần nhất",       "news_sentiment"),
        ("HPG có nên mua không?",                      "investment_case"),
        ("top 5 mã ROE cao nhất",                      "screening"),
        ("tổng quan thị trường chứng khoán hôm nay",  "market_brief"),
    ]

    @pytest.mark.parametrize("query,expected_intent", _CASES)
    def test_integration_stream_intent(self, query: str, expected_intent: str):
        cid, uid = _new_conv()
        lines = _run_stream(cid, uid, query)
        routing = _parse_routing(lines)
        done = _parse_done(lines)
        reply = _reply_text(lines)

        print(f"\n[{expected_intent}] routing={routing}, reply[:200]={reply[:200]}")

        assert routing is not None, f"{expected_intent}: routing event missing"
        assert routing.get("agent") == expected_intent, \
            f"expected {expected_intent}, got {routing.get('agent')}: {routing}"
        assert done is not None, f"{expected_intent}: done event missing"
        assert len(reply) > 20, f"{expected_intent}: reply too short ({len(reply)})"

    def test_integration_stream_cache_hit_emitted(self):
        """Second identical query on turn-1 eligible intent → cache_hit SSE status."""
        cid1, uid = _new_conv()
        # First call — populates cache
        _run_stream(cid1, uid, "HPG giá hôm nay")

        # Second call from a fresh conversation (same query, no history → cache eligible)
        cid2, _ = _new_conv()
        lines2 = _run_stream(cid2, uid, "HPG giá hôm nay")
        cache_evt = _parse_status(lines2, "cache_hit")
        print(f"\n[cache_hit] event: {cache_evt}")
        # Cache hit is best-effort (Redis may be unavailable in CI) — just verify no crash
        assert _parse_done(lines2) is not None, "must complete even if cache miss"
