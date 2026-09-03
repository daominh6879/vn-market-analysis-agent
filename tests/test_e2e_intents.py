"""
tests/test_e2e_intents.py — One consolidated end-to-end set for the agent pipeline.

For every intent, feed a realistic Vietnamese user prompt and verify three layers
of the same code path the app runs:

  1. classify_hybrid (real LLM)  → correct intent + ticker
  2. llm_route        (real LLM)  → type="agent", correct intent + ticker
  3. gather_data      (real tools)→ the intent's gather function fired and returned
                                   data carrying its deterministic header marker

This is the "verify code later" regression suite: intent routing + tool wiring + data
shape in one place. It supersedes the scattered intent checks in
tests/test_bai32_six_intents.py and tests/test_agent_integration.py.

Prereqs: Postgres + Redis running, .env with LLM_PROVIDER=deepseek + key.

Run everything (slow, ~2-5 min, real LLM + external APIs):
    python -m pytest tests/test_e2e_intents.py -v -s -m e2e

Run one layer only:
    python -m pytest tests/test_e2e_intents.py -v -m e2e -k "classify"
    python -m pytest tests/test_e2e_intents.py -v -m e2e -k "route"
    python -m pytest tests/test_e2e_intents.py -v -m e2e -k "gather"
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Graph nodes print Vietnamese text; Windows default console is cp1252, so a `print`
# inside decompose_node would raise UnicodeEncodeError and abort the turn.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

pytestmark = pytest.mark.e2e

# Minimal persona for llm_route — routing is tool-driven, not prompt-sensitive.
_ROUTE_SYSTEM = (
    "Bạn là trợ lý phân tích tài chính chứng khoán Việt Nam. "
    "Trả lời bằng tiếng Việt."
)

# ── Intent × prompt × ticker × data-marker table ─────────────────────────────
#
# `marker` is the deterministic header each gather_data() prepends, proving the
# correct gather function ran (and did not crash / return an error stub).
INTENT_CASES = [
    dict(
        intent="price_action",
        prompt="Khối ngoại mua bán ròng HPG hôm nay thế nào?",
        ticker="HPG",
        marker="[GIÁ & DÒNG TIỀN",
    ),
    dict(
        intent="technical_analysis",
        prompt="RSI và MACD của HPG đang như thế nào?",
        ticker="HPG",
        marker="[KỸ THUẬT",
    ),
    dict(
        intent="valuation",
        prompt="P/E của HPG so với trung bình ngành thép là bao nhiêu?",
        ticker="HPG",
        marker="[CƠ BẢN & ĐỊNH GIÁ",
    ),
    dict(
        intent="macro_sector",
        prompt="Tỷ giá USD/VND hôm nay và giá dầu thô thế nào?",
        ticker=None,
        marker="[VĨ MÔ & NGÀNH]",
    ),
    dict(
        intent="news_sentiment",
        prompt="Tin tức về HPG trong 3 ngày gần nhất",
        ticker="HPG",
        marker="[TIN TỨC & SENTIMENT",
    ),
    dict(
        intent="investment_case",
        prompt="HPG có nên mua không? Cho bull case và bear case",
        ticker="HPG",
        marker="[GIÁ & DÒNG TIỀN",
    ),
    dict(
        intent="screening",
        prompt="Top 5 mã có ROE cao nhất trong database",
        ticker=None,
        marker="[SCREENING]",
    ),
    dict(
        intent="breakout_scan",
        prompt="Quét cổ phiếu đang breakout tạo đỉnh mới",
        ticker=None,
        marker="[BREAKOUT",
    ),
]


def _case_id(case: dict) -> str:
    return case["intent"]


# ── Layer 1: intent classification (real LLM) ────────────────────────────────

@pytest.mark.parametrize("case", INTENT_CASES, ids=_case_id)
def test_classify_intent(case):
    """classify_hybrid maps each prompt to its intent (+ ticker when named)."""
    from agents.classifier import classify_hybrid

    r = classify_hybrid(case["prompt"])
    assert r.intent == case["intent"], (
        f"[{case['intent']}] classified as {r.intent!r}: {r.reason}"
    )
    if case["ticker"]:
        assert r.ticker == case["ticker"], (
            f"[{case['intent']}] ticker={r.ticker!r}, expected {case['ticker']!r}"
        )
    print(f"\n  [{case['intent']}] ticker={r.ticker!r} reason={r.reason!r}")


def test_classify_conversation():
    """Pure greeting → conversation intent."""
    from agents.classifier import classify_hybrid

    r = classify_hybrid("Xin chào, bạn tên gì?")
    assert r.intent == "conversation", f"got {r.intent!r}: {r.reason}"


# ── Layer 2: entry routing (real LLM) ────────────────────────────────────────

@pytest.mark.parametrize("case", INTENT_CASES, ids=_case_id)
def test_llm_route_agent_intent(case):
    """llm_route (the real turn entry) routes each prompt to type=agent + intent."""
    from agents.conversation_router import llm_route

    route = llm_route(case["prompt"], [], _ROUTE_SYSTEM)
    assert route.get("type") == "agent", (
        f"[{case['intent']}] routed {route.get('type')!r}, not agent: {route}"
    )
    assert route.get("intent") == case["intent"], (
        f"[{case['intent']}] routed intent {route.get('intent')!r}"
    )
    if case["ticker"]:
        assert route.get("ticker") == case["ticker"], (
            f"[{case['intent']}] ticker={route.get('ticker')!r}, expected {case['ticker']!r}"
        )
    print(f"\n  [{case['intent']}] route ticker={route.get('ticker')!r}")


def test_llm_route_conversation_direct_reply():
    """Pure greeting → direct_reply (type=text), no agent graph run."""
    from agents.conversation_router import llm_route

    route = llm_route("Xin chào, bạn tên gì?", [], _ROUTE_SYSTEM)
    assert route.get("type") == "text", f"expected direct_reply, got {route}"


# ── Layer 3: tools + data (real DB / external APIs, no LLM) ──────────────────

@pytest.mark.parametrize("case", INTENT_CASES, ids=_case_id)
def test_gather_data_returns_correct_tool_data(case):
    """The intent's gather function (as wired in the graph) returns data with its header.

    Uses agents.graph._get_gather_map() — the exact intent → gather mapping the
    sub-query pipeline runs — so this also verifies the wiring, not just the tool.
    """
    from agents.graph import _get_gather_map

    fn = _get_gather_map()[case["intent"]]
    ticker = case["ticker"] or ""
    data = fn(ticker, case["prompt"])

    assert data, f"[{case['intent']}] gather returned empty string"
    assert case["marker"] in data, (
        f"[{case['intent']}] gather missing {case['marker']!r} "
        f"— wrong tool fired or error stub. Got: {data[:200]!r}"
    )
    print(f"\n  [{case['intent']}] gather {len(data)} chars: {data[:120]!r}")


# ── rag_qa (document QA → RAG/SQL) + valuation cross-ticker ─────────────────

def test_classify_rag_qa_document():
    """Financial-report content (revenue/profit) → rag_qa, not valuation."""
    from agents.classifier import classify_hybrid

    r = classify_hybrid("Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?")
    assert r.intent == "rag_qa", f"got {r.intent!r}: {r.reason}"
    assert r.ticker == "HPG"


def test_llm_route_rag_qa_document():
    """llm_route sends report-content query to rag_qa (agent), not valuation."""
    from agents.conversation_router import llm_route

    route = llm_route("Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?", [], _ROUTE_SYSTEM)
    assert route.get("type") == "agent"
    assert route.get("intent") == "rag_qa", f"got {route.get('intent')!r}"


def test_rag_qa_gather_returns_context():
    """rag_qa gather → retrieve_only (SQL + RAG context), non-empty and no crash."""
    from agents.graph import _get_gather_map

    data = _get_gather_map()["rag_qa"]("HPG", "Doanh thu và lợi nhuận HPG năm 2024 là bao nhiêu?")
    assert isinstance(data, str) and len(data.strip()) > 10, f"rag_qa gather empty: {data!r}"
    print(f"\n  [rag_qa] gather {len(data)} chars: {data[:120]!r}")


def test_valuation_gather_cross_ticker():
    """valuation gather with two real tickers → [SO SÁNH ...] peer table."""
    from agents.intents.fundamentals import gather_data

    data = gather_data("HPG", "So sánh P/E của HPG với VCB")
    assert "[SO SÁNH" in data, f"cross-ticker marker missing: {data[:200]!r}"
    assert "VCB" in data
    print(f"\n  [valuation cross] {data[:120]!r}")


def test_market_brief_graph_produces_report():
    """market_brief runs its own graph (collect_all → compose_outlook → render_report)."""
    from datetime import date

    from agents.market_brief_graph import build_brief_graph, make_initial_state

    app = build_brief_graph()
    final = app.invoke(make_initial_state(date=str(date.today()), output_path=""))
    report = final.get("report_text", "")

    assert report, "market_brief report_text empty"
    assert "📰" in report, "market brief template header missing"
    assert "NHẬN ĐỊNH" in report, "market brief outlook section missing"
    print(f"\n  [market_brief] {len(report)} chars, missing_fields={final.get('missing_fields')}")


# ── regression: sub-query pipeline "loop" ─────────────────────────────────────
#
# The original "loop" was 4 sub-tasks → 4× full gather → 4× news auto-fetch. Two
# fixes now cover it, each with its own guard below:
#   1. Tool-level TTL cache (tools/cache.py, hooked into instrument_tool) dedupes
#      repeated tool calls → the cafef/tavily fetch fires once, not 4×.
#   2. decompose_node assigns each sub-task its own intent (not the parent intent).


def test_run_subqueries_dedupes_news_fetch_via_tool_cache(monkeypatch):
    """4 identical sub-tasks → the expensive news auto-fetch fires ≤1×, not 4×.

    e2e: drives the real gather path (run_subqueries_node → news_sentiment.gather_data
    → search_financial_news). Dedup happens inside instrument_tool's TTL cache, so
    repeated search_financial_news calls short-circuit before re-triggering the
    cafef/tavily fetch. Asserts ≤1 (0 = news already present / validation short-circuit;
    1 = fetched once) — never 4.
    """
    from agents import graph as G
    from tools import price as price_mod

    calls = {"n": 0}

    def spy(ticker, days):
        # Pure counter — do NOT call the real _auto_fetch_ticker_news here: monkeypatch
        # has replaced the module global with this spy, so calling it would recurse.
        calls["n"] += 1

    monkeypatch.setattr(price_mod, "_auto_fetch_ticker_news", spy)

    state = {
        "sub_tasks": [
            {"intent": "news_sentiment", "tickers": ["VCB"], "question": f"tin VCB {i}"}
            for i in range(4)
        ],
        "query": "Tin tức VCB",
        "ticker": "VCB",
    }
    G.run_subqueries_node(state)

    # Two cacheable news tools call _auto_fetch_ticker_news (search_financial_news and
    # analyze_market_sentiment). Deduped → ≤2 total (one each). Without cache → 4×2=8.
    assert calls["n"] <= 2, f"news auto-fetch fired {calls['n']}×, expected ≤2 (cache-deduped)"


def test_decompose_assigns_distinct_intents():
    """decompose_node tags each sub-task with its own intent, not the parent.

    Real LLM (e2e): a multi-angle query must decompose into ≥2 distinct intents.
    Guards against the regression where all sub-tasks inherited intent='investment_case'
    and each re-ran the full gather pipeline.
    """
    from agents.graph import decompose_node

    out = decompose_node({
        "query": (
            "Phân tích toàn diện VCB: kỹ thuật (RSI, MACD), "
            "định giá so ngành ngân hàng, và khuyến nghị nên mua hay bán"
        ),
        "intent": "investment_case",
        "ticker": "VCB",
    })

    sub_tasks = out["sub_tasks"]
    assert sub_tasks, "decompose produced no sub-tasks"

    intents = {t["intent"] for t in sub_tasks}
    assert len(intents) >= 2, (
        f"all sub-tasks share intent {intents!r} — expected ≥2 distinct intents. "
        f"sub_tasks={sub_tasks!r}"
    )
