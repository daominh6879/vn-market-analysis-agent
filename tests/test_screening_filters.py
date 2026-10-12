"""
tests/test_screening_filters.py — screening takes structured filters from the router LLM only (no regex).
"""

from agents.intents.screening import _normalize_filters
import agents.intents.screening as screening_mod


def test_regex_filter_parsers_removed():
    assert not hasattr(screening_mod, "_parse_filters")
    assert not hasattr(screening_mod, "_parse_rsi_filter")


def test_structured_filters_kept_as_given():
    got = _normalize_filters([
        {"indicator": "p/e", "op": "<", "threshold": 10},
        {"indicator": "roe", "op": ">", "threshold": 15},
    ])
    assert [(f["indicator"], f["op"], f["threshold"]) for f in got] == [("p/e", "<", 10.0), ("roe", ">", 15.0)]
