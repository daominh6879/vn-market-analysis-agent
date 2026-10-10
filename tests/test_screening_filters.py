"""
tests/test_screening_filters.py — regex fallback for screening filters (no LLM, no DB).

The deterministic screening route used to keep only RSI and silently drop P/E, ROE...
"""

import pytest

from agents.conversation_router import _screening_route
from agents.intents.screening import _parse_filters


@pytest.mark.parametrize("query,expected", [
    ("lọc cổ phiếu P/E < 10", [("p/e", "<", 10.0)]),
    ("lọc cổ phiếu pe dưới 10", [("pe", "<", 10.0)]),
    ("lọc ROE trên 15", [("roe", ">", 15.0)]),
    ("lọc RSI < 30", [("rsi", "<", 30.0)]),
    ("lọc P/E < 10 và ROE > 15", [("p/e", "<", 10.0), ("roe", ">", 15.0)]),
    ("lọc EPS >= 2000", [("eps", ">=", 2000.0)]),
    ("lọc P/E dưới 8,5", [("p/e", "<", 8.5)]),
    ("lọc cổ phiếu tốt", []),
    ("lọc cổ phiếu trong ngành pepsi", []),  # 'pe' inside a word is not an indicator
])
def test_parse_filters(query, expected):
    got = [(f["indicator"], f["op"], f["threshold"]) for f in _parse_filters(query)]
    assert got == expected


def test_screening_route_keeps_pe_filter():
    r = _screening_route("lọc cổ phiếu P/E < 10")
    assert r["intent"] == "screening"
    assert r["screening"] ==[{"indicator": "p/e", "op": "<", "threshold": 10.0}]
