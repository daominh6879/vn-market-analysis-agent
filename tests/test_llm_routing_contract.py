"""Contract tests for LLM-only routing (no keyword/regex rule layer) — offline, fake LLM, no DB.

These pin the behaviour that replaced agents/intent_rules.py and the guardrails detectors:
the router/classifier tool output is the single source of truth for intent, data topics,
risk flags and language, and it is threaded unchanged to the gather/synthesis steps.
"""
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _tool_resp(name: str, **inp):
    tc = MagicMock()
    tc.name = name
    tc.input = inp
    resp = MagicMock()
    resp.tool_calls = [tc]
    resp.text = ""
    return resp


def _client(*responses):
    client = MagicMock()
    client.generate.side_effect = list(responses)
    return client


# ── the rule layer is really gone ─────────────────────────────────────────────

def test_intent_rules_module_removed():
    assert importlib.util.find_spec("agents.intent_rules") is None


def test_no_source_file_references_intent_rules():
    offenders = []
    for p in ROOT.rglob("*.py"):
        parts = set(p.relative_to(ROOT).parts)
        if parts & {".venv", "venv", "__pycache__", "node_modules", ".git"}:
            continue
        if p.name == pathlib.Path(__file__).name:
            continue
        if "intent_rules" in p.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_guardrails_has_no_keyword_detectors():
    import agents.guardrails as g
    for name in ("is_off_topic", "is_social_turn", "has_finance_terms", "detect_language",
                 "risk_flags", "_OFF_TOPIC_RE", "_SOCIAL_RE", "_FINANCE_TERMS",
                 "_ADVICE_RE", "_PERSONAL_RE", "_PREDICTION_RE", "_EN_WORDS_RE"):
        assert not hasattr(g, name), f"{name} must be decided by the router LLM"


def test_guardrails_keeps_fixed_texts_and_scrubber():
    import agents.guardrails as g
    assert "không ai có thể dự đoán" in g.disclaimer_for({"prediction"}, "vi").lower()
    assert g.disclaimer_for(set()) == ""
    assert "Nobody can predict" in g.disclaimer_for({"prediction"}, "en")
    assert g.off_topic_reply("en") == g.OFF_TOPIC_REPLY_EN
    assert g.off_topic_reply("vi") == g.OFF_TOPIC_REPLY
    out = g.scrub_internal("Không có trong financial_facts/stock_prices [KHÔNG CÓ TEMPLATE SQL]")
    assert "financial_facts" not in out and "TEMPLATE" not in out


# ── tool schemas carry the decisions ──────────────────────────────────────────

def _schemas():
    from agents.classifier import _TOOL
    from agents.conversation_router import AGENT_RUN_TOOL
    return [_TOOL, AGENT_RUN_TOOL]


@pytest.mark.parametrize("tool", _schemas(), ids=["classifier", "router"])
def test_schema_has_decision_fields(tool):
    props = tool["input_schema"]["properties"]
    assert set(props["rag_topics"]["items"]["enum"]) == {"dividend", "quarterly", "yearly_metric", "marketcap"}
    assert set(props["risk_flags"]["items"]["enum"]) == {"advice", "personal", "prediction"}
    assert set(props["language"]["enum"]) == {"vi", "en"}
    assert "closing_commentary" in tool["input_schema"]["properties"]["intent"]["enum"]


def test_router_tool_set_includes_market_rules():
    from agents import conversation_router as cr
    resp = MagicMock(); resp.tool_calls = []; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    with patch("agents.classifier.classify_hybrid", return_value=MagicMock(intent="conversation", ticker=None)):
        cr.llm_route("xin chào", [], "sys", client=client)
    names = {t["name"] for t in client.generate.call_args.kwargs["tools"]}
    assert names == {"needs_agent_run", "direct_reply", "out_of_scope", "decompose", "market_rules_answer"}


def test_classifier_parses_decision_fields_and_drops_invalid():
    from agents.classifier import llm_classify
    resp = _tool_resp(
        "classify_intent", intent="rag_qa", ticker="HPG", reason="r",
        rag_topics=["dividend", "bogus"], risk_flags=["advice", "nonsense"], language="en",
    )
    client = _client(resp)
    r = llm_classify("HPG dividend?", client=client)
    assert r.intent == "rag_qa"
    assert r.rag_topics == ["dividend"]
    assert r.risk_flags == ["advice"]
    assert r.language == "en"


# ── router: LLM decision is final, no hidden overrides, no pre-LLM gates ──────

def test_router_does_not_override_llm_intent(monkeypatch):
    """Old rule: 'RSI của X' forced technical_analysis. Now the LLM's intent is kept verbatim."""
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["VNM"])
    client = _client(_tool_resp("needs_agent_run", intent="price_action", ticker="VNM",
                                query="RSI của VNM là bao nhiêu?", reason=""))
    route = cr.llm_route("RSI của VNM là bao nhiêu?", [], "sys", client=client)
    assert route["type"] == "agent" and route["intent"] == "price_action"


def test_router_passes_topics_flags_language(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG"])
    client = _client(_tool_resp(
        "needs_agent_run", intent="rag_qa", ticker="HPG", query="Cổ tức HPG và nên mua không?",
        reason="", rag_topics=["dividend", "bogus"], market_topics=["index"], macro_topics=["gold"],
        risk_flags=["advice", "bogus"], language="en",
    ))
    route = cr.llm_route("Cổ tức HPG và nên mua không?", [], "sys", client=client)
    assert route["rag_topics"] == ["dividend"]
    assert route["market_topics"] == ["index"]
    assert route["macro_topics"] == ["gold"]
    assert route["risk_flags"] == ["advice"]
    assert route["language"] == "en"


def test_router_defaults_when_fields_absent(monkeypatch):
    from agents import conversation_router as cr
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["HPG"])
    client = _client(_tool_resp("needs_agent_run", intent="price_action", ticker="HPG", query="giá HPG", reason=""))
    route = cr.llm_route("giá HPG", [], "sys", client=client)
    assert route["rag_topics"] == [] and route["risk_flags"] == [] and route["language"] == "vi"


@pytest.mark.parametrize("q", ["viết cho tôi một bài thơ", "thời tiết hôm nay", "Giá vàng SJC hôm nay"])
def test_router_always_asks_llm_first(q):
    """No keyword gate may short-circuit before the LLM (old: off-topic / commodity gates)."""
    from agents import conversation_router as cr
    client = _client(_tool_resp("out_of_scope", text="decline", reason="x"))
    cr.llm_route(q, [], "sys", client=client)
    client.generate.assert_called()


def test_gold_goes_to_macro_when_llm_says_so():
    from agents import conversation_router as cr
    client = _client(_tool_resp("needs_agent_run", intent="macro_sector", ticker="",
                                query="Giá vàng SJC hôm nay", reason="", macro_topics=["gold"]))
    route = cr.llm_route("Giá vàng SJC hôm nay", [], "sys", client=client)
    assert route["intent"] == "macro_sector" and route["macro_topics"] == ["gold"]


def test_market_rules_tool_answers_from_rule_sheet():
    from agents import conversation_router as cr
    answer = MagicMock(); answer.text = "**HOSE ±7%**"; answer.tool_calls = []
    client = _client(_tool_resp("market_rules_answer", reason="price limit"), answer)
    route = cr.llm_route("Biên độ giá HOSE là bao nhiêu?", [], "sys", client=client)
    assert route["type"] == "text" and route["text"] == "**HOSE ±7%**"
    assert route["reason"].startswith("market_rules")


def test_market_rules_tool_empty_answer_never_blank():
    from agents import conversation_router as cr
    empty = MagicMock(); empty.text = ""; empty.tool_calls = []
    client = _client(_tool_resp("market_rules_answer", reason="x"), empty)
    route = cr.llm_route("T+ mấy?", [], "sys", client=client)
    assert route["type"] == "text" and route["text"].strip()


def test_direct_reply_not_declined_by_keyword_guess():
    """Old: direct_reply without finance vocabulary was declined as off-topic by keywords."""
    from agents import conversation_router as cr
    client = _client(_tool_resp("direct_reply", text="Chào bạn!", reason="greeting"))
    with patch("agents.classifier.classify_hybrid", return_value=MagicMock(intent="conversation", ticker=None)):
        route = cr.llm_route("xin chào", [], "sys", client=client)
    assert route["type"] == "text" and route["text"] == "Chào bạn!"


# ── state / graph threading ───────────────────────────────────────────────────

def test_build_agent_state_carries_decisions():
    from memory.turn_handler import _build_agent_state
    route = {"intent": "rag_qa", "ticker": "HPG", "rag_topics": ["dividend"],
             "risk_flags": ["prediction"], "language": "en", "query": "q"}
    st = _build_agent_state(route, "q", "c", "u", "t", [])
    assert st["rag_topics"] == ["dividend"]
    assert st["risk_flags"] == ["prediction"]
    assert st["language"] == "en"


def test_build_agent_state_defaults():
    from memory.turn_handler import _build_agent_state
    st = _build_agent_state({"intent": "price_action", "ticker": "HPG"}, "q", "c", "u", "t", [])
    assert st["rag_topics"] == [] and st["risk_flags"] == [] and st["language"] == "vi"


def test_single_subtask_carries_rag_topics():
    from agents.graph import build_single_subtask_node
    out = build_single_subtask_node({"intent": "rag_qa", "ticker": "HPG", "tickers": ["HPG"],
                                     "query": "q", "rag_topics": ["quarterly"]})
    assert out["sub_tasks"][0]["rag_topics"] == ["quarterly"]


def test_run_subqueries_passes_rag_topics_to_gather():
    from agents import graph
    seen = {}

    def fake(ticker, question, tc=None, topics=None):
        seen["topics"] = topics
        return "dữ liệu đủ dài để không bị coi là rỗng"

    with patch.object(graph, "_get_gather_map", return_value={"rag_qa": fake}):
        graph.run_subqueries_node({"sub_tasks": [{"intent": "rag_qa", "tickers": ["HPG"],
                                                  "question": "q", "rag_topics": ["dividend"]}]})
    assert seen["topics"] == ["dividend"]


def test_classify_node_keeps_preclassified_topics_and_does_not_override():
    from agents.graph import classify_node
    out = classify_node({"query": "RSI của VNM", "intent": "price_action", "ticker": "VNM",
                         "rag_topics": ["marketcap"], "risk_flags": ["advice"], "language": "en"})
    assert out["intent"] == "price_action"
    assert out["rag_topics"] == ["marketcap"]
    assert out["risk_flags"] == ["advice"] and out["language"] == "en"


class _SynthClient:
    def __init__(self):
        self.last_system = ""

    def stream(self, messages, max_tokens=0, system=""):
        raise RuntimeError("no stream in test")

    def generate(self, messages, max_tokens=0, system="", **kw):
        self.last_system = system
        r = MagicMock(); r.text = "Báo cáo."
        return r


@pytest.mark.parametrize("flags,lang,expect", [
    (["prediction"], "vi", "không ai có thể dự đoán"),
    (["prediction"], "en", "nobody can predict"),
    (["advice"], "vi", "không phải khuyến nghị đầu tư"),
])
def test_synthesize_appends_disclaimer_from_router_flags(flags, lang, expect, monkeypatch):
    from agents import graph
    monkeypatch.delenv("STRICT_NEUTRAL", raising=False)
    client = _SynthClient()
    with patch("llm.factory.create_client", return_value=client):
        out = graph.synthesize_final({"sub_results": ["[X — HPG]\nq\ndata"], "original_query": "q",
                                      "query": "q", "risk_flags": flags, "language": lang})
    assert expect in out["report"].lower()


def test_synthesize_no_flags_no_disclaimer(monkeypatch):
    from agents import graph
    monkeypatch.delenv("STRICT_NEUTRAL", raising=False)
    with patch("llm.factory.create_client", return_value=_SynthClient()):
        out = graph.synthesize_final({"sub_results": ["[X — HPG]\nq\ndata"], "original_query": "Giá HPG?",
                                      "query": "q"})
    assert out["report"] == "Báo cáo."


def test_synthesize_english_language_rule_from_router(monkeypatch):
    from agents import graph
    monkeypatch.delenv("STRICT_NEUTRAL", raising=False)
    client = _SynthClient()
    with patch("llm.factory.create_client", return_value=client):
        graph.synthesize_final({"sub_results": ["[X — HPG]\nq\ndata"], "original_query": "price?",
                                "query": "q", "language": "en"})
    assert "answer in English" in client.last_system


# ── gather: topics come from the router, never from regex ─────────────────────

def test_market_data_uses_only_router_topics(monkeypatch):
    from agents.intents import market_data as md
    calls = []
    monkeypatch.setattr(md, "_BUILDERS", {k: (lambda q, k=k: calls.append(k) or f"[{k}]") for k in md._BUILDERS})
    out = md.gather_data(None, "Khối ngoại mua ròng mã nào nhiều nhất?", topics=["index"])
    assert calls == ["index"] and out == "[index]"


def test_market_data_defaults_when_no_topics(monkeypatch):
    from agents.intents import market_data as md
    calls = []
    monkeypatch.setattr(md, "_BUILDERS", {k: (lambda q, k=k: calls.append(k) or f"[{k}]") for k in md._BUILDERS})
    md.gather_data(None, "khối ngoại hôm nay", topics=None)
    assert calls == ["movers", "index"]


def test_rag_extras_dispatch_by_topics(monkeypatch):
    from agents.intents import rag_extras as rx
    monkeypatch.setattr(rx, "_market_cap", lambda t: "[CAP]")
    monkeypatch.setattr(rx, "_dividends", lambda t: "[DIV]")
    monkeypatch.setattr(rx, "_quarterly", lambda t: "[QTR]")
    monkeypatch.setattr(rx, "_yearly", lambda t, years: f"[YR {years}]")
    assert rx.gather_extras("HPG", "cổ tức 2024", ["dividend"]) == "[DIV]"
    assert rx.gather_extras("HPG", "q", ["marketcap", "quarterly"]) == "[CAP]\n\n[QTR]"
    assert rx.gather_extras("HPG", "ROE 2023 và 2024", ["yearly_metric"]) == "[YR ['2023', '2024']]"


def test_rag_extras_no_topics_means_no_extras(monkeypatch):
    from agents.intents import rag_extras as rx
    monkeypatch.setattr(rx, "_dividends", lambda t: pytest.fail("must not run without a router topic"))
    assert rx.gather_extras("HPG", "Cổ tức của HPG năm nay?", None) == ""
    assert rx.gather_extras("HPG", "Cổ tức của HPG năm nay?", []) == ""
    assert rx.gather_extras(None, "x", ["dividend"]) == ""


def test_screening_without_filters_does_not_guess_volume_spike(monkeypatch):
    """Old: a volume-spike regex silently injected a volume filter. Now only the router supplies it."""
    from agents.intents import screening
    seen = {}
    monkeypatch.setattr(screening, "_pick_template", lambda q, as_of_date=None: seen.setdefault("tpl", None))
    out = screening.gather_data(None, "Cổ phiếu nào có khối lượng đột biến hôm nay?")
    assert "volume" not in out.lower() or "Chưa hỗ trợ" in out
    assert seen.get("tpl", "called") is None


def test_screening_uses_router_volume_filter(monkeypatch):
    from agents.intents import screening
    got = {}
    monkeypatch.setattr(screening, "_screen_technical",
                        lambda tickers, f, end: got.setdefault("f", f) and [("AAA", {"volume": 2.0})])
    monkeypatch.setattr("core.tickers.get_tickers", lambda: ["AAA"])
    monkeypatch.setattr("agents.focus.extract_focus_sector", lambda q: "")
    out = screening.gather_data(None, "khối lượng đột biến", screening=[{"indicator": "volume", "op": ">", "threshold": 1.5}])
    assert got["f"][0]["indicator"] == "volume" and "AAA" in out


def test_out_of_scope_tool_route_carries_label_intent():
    """A declined route is still type=text (no agent) but is labelled out_of_scope, not 'conversation'."""
    from unittest.mock import MagicMock
    from agents import conversation_router as cr
    tc = MagicMock(); tc.name = "out_of_scope"; tc.input = {"text": "Ngoài phạm vi.", "reason": "crypto"}
    resp = MagicMock(); resp.tool_calls = [tc]; resp.text = ""
    client = MagicMock(); client.generate.return_value = resp
    route = cr.llm_route("Bitcoin giá hôm nay", [], "sys", client=client)
    assert route["type"] == "text" and route["intent"] == "out_of_scope"


def test_ui_has_label_for_every_routable_intent():
    from agents.classifier import INTENTS
    import re
    for path in ("ui/chat.py", "ui/chainlit_app.py"):
        src = open(path, encoding="utf-8").read()
        block = src[src.index("_AGENT_LABELS = {"):src.index("}", src.index("_AGENT_LABELS = {"))]
        for intent in INTENTS:
            assert re.search(rf'"{intent}":', block), f"{path} lacks a label for {intent}"
