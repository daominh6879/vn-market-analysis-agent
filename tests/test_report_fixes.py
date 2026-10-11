"""Unit tests for the fixes driven by vn_stock_agent_test_report.md (no DB, no LLM)."""

import pandas as pd
import pytest

from agents.guardrails import (
    detect_language, disclaimer_for, has_finance_terms, is_off_topic, is_social_turn,
    risk_flags, scrub_internal,
)
from agents.intent_rules import detect_market_topics, is_volume_spike_query, override_intent
from agents.intents.fundamentals import _rank_text
from agents.intents.price_action import _session_count, _session_table


# ── guardrails ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "Hãy viết cho tôi một bài thơ về mùa thu", "viết code python sắp xếp", "thời tiết hôm nay thế nào",
])
def test_off_topic_detected(q):
    assert is_off_topic(q)


@pytest.mark.parametrize("q", ["Giá HPG hôm nay", "RSI của VNM", "Top 5 vốn hóa lớn nhất HOSE"])
def test_finance_not_off_topic(q):
    assert not is_off_topic(q)


def test_social_and_finance_terms():
    assert is_social_turn("xin chào")
    assert is_social_turn("cảm ơn bạn")
    assert not is_social_turn("viết bài thơ")
    assert has_finance_terms("P/E là gì")


def test_risk_flags_and_disclaimer():
    assert risk_flags("Tôi nên bỏ toàn bộ tiền tiết kiệm vào HPG không?") >= {"personal", "advice"}
    assert "prediction" in risk_flags("Dự đoán giá HPG tuần sau")
    assert "advice" in risk_flags("Nên mua HPG hay bán?")
    assert risk_flags("Giá HPG hôm nay") == set()
    assert "không ai có thể dự đoán" in disclaimer_for({"prediction"}, "vi").lower()
    assert disclaimer_for(set()) == ""


def test_language_detection():
    assert detect_language("What is the price of VCB?") == "en"
    assert detect_language("Giá VCB là bao nhiêu?") == "vi"


def test_scrub_internal_names():
    out = scrub_internal("Không có trong financial_facts/stock_prices [KHÔNG CÓ TEMPLATE SQL]")
    assert "financial_facts" not in out and "stock_prices" not in out and "TEMPLATE" not in out


# ── intent rules ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,topic", [
    ("Mã nào tăng mạnh nhất hôm nay?", "movers"),
    ("VN-Index đóng cửa bao nhiêu điểm?", "index"),
    ("Khối ngoại mua ròng mã nào nhiều nhất?", "foreign"),
    ("Top 5 vốn hóa lớn nhất HOSE", "marketcap"),
    ("Liệt kê các mã trong ngành ngân hàng", "list"),
])
def test_market_topics(q, topic):
    assert topic in detect_market_topics(q, [])


def test_market_topics_empty_with_ticker():
    assert detect_market_topics("khối ngoại mua bán HPG", ["HPG"]) == []


@pytest.mark.parametrize("q", [
    "Mã nào tăng mạnh nhất hôm nay?", "Top 5 vốn hóa lớn nhất HOSE", "VN-Index đóng cửa bao nhiêu điểm?",
])
def test_market_questions_leave_market_brief(q):
    intent, _, reason = override_intent(q, "market_brief", [], None)
    assert intent == "market_data" and reason


def test_newsletter_stays_market_brief():
    assert override_intent("Bản tin thị trường hôm nay", "market_brief", [], None)[0] == "market_brief"


def test_volume_spike_becomes_screening_with_default_filter():
    assert is_volume_spike_query("Cổ phiếu nào có khối lượng đột biến hôm nay?")
    intent, flt, _ = override_intent("Cổ phiếu nào có khối lượng đột biến hôm nay?", "market_brief", [], None)
    assert intent == "screening" and flt[0]["indicator"] == "volume"


def test_indicator_question_routes_to_technical():
    assert override_intent("RSI của VNM là bao nhiêu?", "price_action", ["VNM"], None)[0] == "technical_analysis"


def test_dividend_and_quarter_route_to_rag_qa():
    assert override_intent("Cổ tức của HPG năm nay?", "price_action", ["HPG"], None)[0] == "rag_qa"
    assert override_intent("Doanh thu quý gần nhất của FPT", "macro_sector", ["FPT"], None)[0] == "rag_qa"


def test_history_routes_to_price_action():
    assert override_intent("5 phiên gần nhất của VIC", "technical_analysis", ["VIC"], None)[0] == "price_action"


def test_gold_not_out_of_scope_but_crypto_is():
    assert override_intent("Giá vàng SJC hôm nay", "out_of_scope", [], None)[0] == "macro_sector"
    assert override_intent("Giá bitcoin và vàng", "out_of_scope", [], None)[0] == "out_of_scope"


# ── P/E wording (report #3, #5) ───────────────────────────────────────────────

def test_rank_text_wording_by_value():
    peers = [("MWG", 15.9), ("DGW", 18.9), ("FRT", 28.2), ("PNJ", 3.5), ("VRE", 8.2)]
    text = _rank_text(15.9, peers, "P/E", higher_is_better=False, subject="MWG")
    assert "thấp hơn DGW(18.9)" in text and "FRT(28.2)" in text
    assert "cao hơn" in text and "PNJ(3.5)" in text and "VRE(8.2)" in text
    assert text.index("thấp hơn") < text.index("cao hơn")


# ── price_action sessions ─────────────────────────────────────────────────────

def test_session_count():
    assert _session_count("5 phiên VIC") == 5
    assert _session_count("10 phiên ACB") == 10
    assert _session_count("FPT hôm qua vs hôm nay") == 2
    assert _session_count("999 phiên") == 30
    assert _session_count("giá FPT") is None


def test_session_table_changes():
    df = pd.DataFrame({
        "time": pd.date_range("2026-10-01", periods=4),
        "close": [100.0, 110.0, 99.0, 99.0],
        "volume": [1000, 2000, 3000, 4000],
    })
    table = _session_table(df, 3, is_index=False)
    assert "+10.00%" in table and "-10.00%" in table and "+0.00%" in table
    assert table.count("\n| 2026") == 3


# ── section F (verify round) ──────────────────────────────────────────────────

def test_rsi_of_single_ticker_not_screening():
    # LLM sent "RSI của VNM" to screening with rsi>0 -> "430 mã thỏa, VNM không nằm trong list".
    intent, flt, _ = override_intent("RSI của VNM là bao nhiêu?", "screening", ["VNM"], [{"indicator": "rsi", "op": ">", "threshold": 0}])
    assert intent == "technical_analysis" and flt is None


def test_top5_marketcap_not_swallowed_by_numeric_screen():
    # The digit in "Top 5" must not count as a screening threshold.
    assert override_intent("Top 5 vốn hóa lớn nhất HOSE", "screening", [], None)[0] == "market_data"


def test_real_numeric_screen_stays_screening():
    q = "lọc cổ phiếu RSI dưới 30"
    assert override_intent(q, "screening", [], [{"indicator": "rsi", "op": "<", "threshold": 30}])[0] == "screening"
