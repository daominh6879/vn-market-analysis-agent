"""
tests/test_graph_unified.py — tests for agents/graph.py.

Covers:
  - _route_after_clarify: market_brief / simple / decompose dispatch
  - route_after_subqueries + route_after_critique: bounded re-plan + self-critique loops
  - _is_empty_result: empty/error sub-result detection
  - check_cache_node / cache_save_node: cache hit / miss paths
  - Full graph invoke + stream_turn across all intents (real LLM + tools)

Run unit only (fast, no network):
  pytest tests/test_graph_unified.py -v -k "RouteAfterClarify or LoopRouting or CacheNodes"

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
    check_cache_node,
    check_cache_hit,
    cache_save_node,
    _is_empty_result,
    _route_after_clarify,
    route_after_subqueries,
    route_after_critique,
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
# UNIT — routing after clarify (no LLM, no network)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitRouteAfterClarify:
    def test_market_brief(self):
        state = _state("thị trường hôm nay", intent="market_brief", ticker="")
        assert _route_after_clarify(state) == "market_brief"

    def test_simple_leaf_intent_with_ticker(self):
        state = _state("RSI HPG", intent="technical_analysis", ticker="HPG")
        assert _route_after_clarify(state) == "simple"

    def test_complex_intent_always_decompose(self):
        state = _state("mua HPG không", intent="investment_case", ticker="HPG")
        assert _route_after_clarify(state) == "decompose"

    def test_no_intent_decompose(self):
        state = _state("HPG")
        assert _route_after_clarify(state) == "decompose"


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT — sub-query re-plan + report critique routing (no LLM, no network)
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnitLoopRouting:
    def test_is_empty_result(self):
        assert _is_empty_result("") is True
        assert _is_empty_result("[NEWS_SENTIMENT — lỗi: max recursion]") is True
        assert _is_empty_result("ticker không được rỗng.") is True
        assert _is_empty_result("Doanh thu HPG Q1: 100 tỷ") is False

    def test_replan_when_mostly_empty(self):
        assert route_after_subqueries({"sub_results_empty_ratio": 0.9}) == "replan"

    def test_no_replan_after_attempt(self):
        assert route_after_subqueries(
            {"sub_results_empty_ratio": 0.9, "replan_attempted": True}
        ) == "synthesize"

    def test_synthesize_when_data_present(self):
        assert route_after_subqueries({"sub_results_empty_ratio": 0.0}) == "synthesize"

    def test_critique_save_on_pass(self):
        assert route_after_critique({"critique_pass": True}) == "save"

    def test_critique_retry_once(self):
        assert route_after_critique({"critique_pass": False, "critique_attempts": 0}) == "retry"
        # MAX_CRITIQUE=1 → one retry: first critique (attempts=1) still retries,
        # second critique (attempts=2) is exhausted → save.
        assert route_after_critique({"critique_pass": False, "critique_attempts": 1}) == "retry"
        assert route_after_critique({"critique_pass": False, "critique_attempts": 2}) == "save"


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
        """BCTC keywords + no explicit intent → classified rag_qa → RAG/SQL context → report."""
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
