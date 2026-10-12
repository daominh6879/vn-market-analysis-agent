"""tests/test_closing_commentary.py — closing_commentary gather (offline, no LLM / DB)."""

from agents.intents import closing_commentary as cc


def _patch_blocks(monkeypatch, index_text):
    from agents.intents import market_data as md, macro_sector as ms
    monkeypatch.setattr(md, "_index", lambda c: f"{index_text} {c}")
    monkeypatch.setattr(md, "_movers", lambda q: "[MOVERS]")
    monkeypatch.setattr(md, "_liquidity", lambda q: "[LIQ]")
    monkeypatch.setattr(md, "_foreign", lambda q: "[FOREIGN]")
    monkeypatch.setattr(ms, "_sector_performance_text", lambda: "sector perf")
    monkeypatch.setattr(cc, "_index_levels", lambda code: "[LEVELS]")


def test_gather_contains_all_four_content_blocks(monkeypatch):
    _patch_blocks(monkeypatch, "[INDEX]")
    out = cc.gather_data(None, "nhận định cuối phiên")
    for block in ("[INDEX] VNINDEX", "[LEVELS]", "[MOVERS]", "[LIQ]", "[FOREIGN]", "sector perf"):
        assert block in out


def test_gather_without_index_data_reports_no_data(monkeypatch):
    _patch_blocks(monkeypatch, "Không có dữ liệu")
    assert "Không có dữ liệu" in cc.gather_data(None, "nhận định cuối phiên")


def test_closing_rule_demands_four_sections_and_conditional_scenarios():
    assert "4 mục" in cc.CLOSING_RULE and "kịch bản" in cc.CLOSING_RULE.lower()
