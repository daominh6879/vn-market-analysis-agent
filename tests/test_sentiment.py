"""
tests/test_sentiment.py — Unit tests for classify_sentiment().

No LLM calls, no DB, no Qdrant needed.
Sentiment classified at retrieve time (not index time) — no Qdrant payload field.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from rag.news_index import VALID_SENTIMENTS, classify_sentiment


def _mock_llm(response_text: str):
    client = MagicMock()
    resp = MagicMock()
    resp.text = response_text
    client.generate.return_value = resp
    return client


class TestClassifySentiment:
    def test_valid_label_returned(self):
        with patch("rag.news_index.create_client", return_value=_mock_llm("positive")):
            assert classify_sentiment("HPG lợi nhuận tăng") == "positive"

    def test_fallback_on_unknown_label(self):
        with patch("rag.news_index.create_client", return_value=_mock_llm("VERY BULLISH")):
            result = classify_sentiment("text")
        assert result == "neutral"

    def test_fallback_on_empty_response(self):
        with patch("rag.news_index.create_client", return_value=_mock_llm("")):
            result = classify_sentiment("text")
        assert result == "neutral"

    def test_fallback_on_exception(self):
        with patch("rag.news_index.create_client", side_effect=RuntimeError("no LLM")):
            result = classify_sentiment("text")
        assert result == "neutral"

    @pytest.mark.parametrize("raw,expected", [
        ("positive",   "positive"),
        ("NEGATIVE",   "negative"),
        ("Neutral\n",  "neutral"),
        ("negative  ", "negative"),
    ])
    def test_label_normalization(self, raw: str, expected: str):
        with patch("rag.news_index.create_client", return_value=_mock_llm(raw)):
            assert classify_sentiment("text") == expected

    def test_result_always_in_valid_set(self):
        for label in ("positive", "neutral", "negative", "xyz", ""):
            with patch("rag.news_index.create_client", return_value=_mock_llm(label)):
                result = classify_sentiment("text")
            assert result in VALID_SENTIMENTS


def test_valid_sentiments_set():
    assert VALID_SENTIMENTS == {"positive", "neutral", "negative"}

def test_valid_sentiments_is_frozenset():
    assert isinstance(VALID_SENTIMENTS, frozenset)
