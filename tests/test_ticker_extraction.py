"""
tests/test_ticker_extraction.py — pure unit tests for core.tickers.extract_tickers edge cases
(VND as FX vs VNDirect, ordinary words that collide with tickers, mixed-case queries).

No DB / network: the universe and the alias index are monkeypatched.
"""

from __future__ import annotations

import pytest

from core.tickers import extract_tickers

UNIVERSE = ["HPG", "VNM", "FPT", "MWG", "SSI", "VND", "VCB", "GAS", "OIL", "TRA", "HAS", "DAT", "HRC"]


@pytest.fixture(autouse=True)
def _universe(monkeypatch):
    monkeypatch.setattr("core.tickers.get_tickers", lambda: list(UNIVERSE))
    monkeypatch.setattr("core.tickers._load_name_index", lambda: {"hoa phat": "HPG", "vinamilk": "VNM"})


# ── VND: the dong in FX/amount contexts, VNDirect everywhere else ──────────────

def test_vnd_as_ticker_in_comparison():
    assert extract_tickers("compare SSI and VND P/B ratio") == ["SSI", "VND"]
    assert extract_tickers("SSI vs VND P/B and EPS") == ["SSI", "VND"]
    assert extract_tickers("giá cổ phiếu VND hôm nay") == ["VND"]


@pytest.mark.parametrize("q", [
    "tỷ giá USD/VND hôm nay",
    "VND/USD tuần qua",
    "usd vnd 3 tháng qua tăng bao nhiêu %",
    "giá vàng 80 triệu VND",
    "giá 20.000 VND",
])
def test_vnd_as_currency_is_not_a_ticker(q):
    assert extract_tickers(q) == []


def test_vnd_currency_does_not_hide_a_real_ticker():
    assert extract_tickers("tỷ giá USD/VND ảnh hưởng gì đến FPT?") == ["FPT"]
    assert extract_tickers("SSI giá 5 tỷ VND") == ["SSI"]


# ── ordinary words that are also tickers count only in upper case ─────────────

@pytest.mark.parametrize("q", [
    "How is the oil price trend affecting the oil and gas sector this quarter?",
    "giá tôm cá tra mới nhất",
    "who has the best valuation",
    "chi so VN-Index dat bao nhieu diem",
])
def test_lowercase_common_words_are_not_tickers(q):
    assert extract_tickers(q) == []


def test_uppercase_collision_word_is_still_a_ticker():
    assert extract_tickers("giá GAS hôm nay") == ["GAS"]
    assert extract_tickers("OIL và HPG") == ["OIL", "HPG"]


def test_collision_word_does_not_block_company_name():
    assert extract_tickers("Hoa Phat co tra co tuc nam 2024 khong") == ["HPG"]


# ── mixed case: an upper-case non-ticker word must not hide a lower-case ticker ─

def test_uppercase_non_ticker_word_does_not_hide_lowercase_ticker():
    assert extract_tickers("MACD ssi tuần trước") == ["SSI"]
    assert extract_tickers("ROE của hpg") == ["HPG"]
    assert extract_tickers("RSI vnm hôm nay") == ["VNM"]


def test_company_name_with_d_bar_matches_without_diacritics(monkeypatch):
    # alias is stored from the DB with "đ"; the user types "dong"
    monkeypatch.setattr("core.tickers._load_name_index", lambda: {"the gioi di dong": "MWG"})
    from core import tickers
    monkeypatch.setattr(tickers, "_load_name_index", lambda: {tickers._normalize("Thế Giới Di Động"): "MWG"})
    assert extract_tickers("the gioi di dong 30 ngay qua gia the nao") == ["MWG"]
    assert extract_tickers("thế giới di động có tăng không") == ["MWG"]
    assert tickers._normalize("Đầu Tư Công") == "dau tu cong"


def test_vn_milk_alias_is_curated_for_vnm(monkeypatch):
    from core import tickers
    from data.hose_universe import TICKER_ENRICHMENT
    aliases = TICKER_ENRICHMENT["VNM"]["name_aliases"]
    monkeypatch.setattr(tickers, "_load_name_index", lambda: {tickers._normalize(a): "VNM" for a in aliases})
    assert extract_tickers("VN milk giá bao nhiêu hôm nay") == ["VNM"]


def test_existing_behaviour_unchanged():
    assert extract_tickers("phân tích HPG và VCB") == ["HPG", "VCB"]
    assert extract_tickers("hpg vcb") == ["HPG", "VCB"]
    assert extract_tickers("phân tích HPG và vinamilk") == ["HPG"]  # names ignored when a code is present
    assert extract_tickers("phân tích vinamilk") == ["VNM"]
    assert extract_tickers("giá thép HRC thế giới") == []  # commodity grade, not the stock
    assert extract_tickers("") == []
