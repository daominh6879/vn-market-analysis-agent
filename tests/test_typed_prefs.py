"""Unit tests for typed long-term preferences (memory/extractor.py, memory/reader.py)."""

from unittest.mock import MagicMock

from memory.extractor import extract_preferences
from memory.reader import _parse_typed


def _mock_client_with_text(text):
    resp = MagicMock()
    resp.text = text
    client = MagicMock()
    client.generate.return_value = resp
    return client


def test_extract_preferences_keeps_favorite_tickers_list(monkeypatch):
    monkeypatch.setattr(
        "memory.extractor.create_client",
        lambda: _mock_client_with_text(
            '[{"key": "favorite_tickers", "value": ["FPT", "vcb"], '
            '"confidence": 0.9, "source_message": "tôi thích FPT và VCB"}]'
        ),
    )
    items = extract_preferences([
        {"role": "user", "content": "tôi thích FPT và VCB"},
        {"role": "assistant", "content": "ok"},
    ])

    assert len(items) == 1
    assert items[0].key == "favorite_tickers"
    assert items[0].value == ["FPT", "vcb"]  # list preserved, not str()-ified


def test_extract_preferences_string_value_unchanged(monkeypatch):
    monkeypatch.setattr(
        "memory.extractor.create_client",
        lambda: _mock_client_with_text(
            '[{"key": "preferred_market", "value": "VN", '
            '"confidence": 0.8, "source_message": "tôi chỉ giao dịch VN"}]'
        ),
    )
    items = extract_preferences([
        {"role": "user", "content": "tôi chỉ giao dịch VN"},
        {"role": "assistant", "content": "ok"},
    ])

    assert items[0].value == "VN"


def test_parse_typed_parses_rows():
    rows = [
        {"key": "preferred_market", "value": '"VN"'},
        {"key": "favorite_tickers", "value": '["FPT", "vcb", "FPT"]'},
        {"key": "preferred_analysis", "value": '"technical"'},
        {"key": "other_key", "value": '"x"'},
    ]
    typed = _parse_typed(rows)

    assert typed["preferred_market"] == "VN"
    assert typed["favorite_tickers"] == ["FPT", "VCB"]  # uppercased + deduped
    assert typed["preferred_analysis"] == "technical"


def test_parse_typed_empty_and_bad_json():
    assert _parse_typed([]) == {
        "preferred_market": "",
        "favorite_tickers": [],
        "preferred_analysis": "",
    }
    typed = _parse_typed([{"key": "favorite_tickers", "value": "not json"}])
    assert typed["favorite_tickers"] == []
