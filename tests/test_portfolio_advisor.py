"""Unit tests for portfolio/advisor.py: verdict parsing, limits, report assembly (no DB, no real LLM)."""
from __future__ import annotations

import json

import pytest

from portfolio import advisor
from portfolio.advisor import MAX_TICKER_WEIGHT, apply_limits, parse_verdicts, size_lots
from portfolio.evidence import compute_risk

NAV = 1_000_000_000.0


def pos(ticker, qty=1000, price=20_000.0, pnl=0.0, sellable=None, sector="Bank"):
    value = qty * price
    return {"ticker": ticker, "quantity": qty, "sellable_quantity": qty if sellable is None else sellable,
            "avg_cost": price / (1 + pnl / 100), "last_price": price, "market_value": value,
            "unrealized_pnl": 0.0, "unrealized_pct": pnl, "realized_pnl": 0.0, "sector": sector, "weight": 0.5}


def cand(ticker, price=30_000.0, sector="Tech"):
    return {"ticker": ticker, "sector": sector, "price": price, "avg_value_bn": 50.0, "ret_20": 0.05,
            "foreign": {"net_5d_bn": 1.0, "net_20d_bn": 2.0}, "sector_nav_pct": 0.0}


def verdict(ticker, action, **kw):
    return {"ticker": ticker, "action": action, "confidence": 0.7, "reasons": ["r"], "risks": [], "cited_news_ids": [], **kw}


def ev(positions, candidates=(), cash=500_000_000.0, nav=NAV):
    return {"positions": positions, "candidates": list(candidates), "cash": {"cash": cash, "nav": nav}}


def act(res, ticker):
    return next(a for a in res["actions"] if a["ticker"] == ticker)


# ── parse_verdicts ────────────────────────────────────────────────────────────

def llm_json(verdicts, **extra):
    return json.dumps({"market": "m", "verdicts": verdicts, "ideas_summary": "i", "risk_summary": "r", **extra})


V = {"AAA": {1, 2}, "BBB": set(), "CCC": set()}


def test_parse_valid_and_fenced_json():
    txt = "<think>x</think>```json\n" + llm_json([{"ticker": "aaa", "action": "cut_loss", "confidence": 0.9,
                                                    "reasons": ["lỗ 10%"], "cited_news_ids": [1]}]) + "\n```"
    summ, out = parse_verdicts(txt, ["AAA"], [], V)
    assert out["AAA"]["action"] == "CUT_LOSS" and out["AAA"]["cited_news_ids"] == [1] and summ["market"] == "m"


def test_parse_rejects_wrong_action_kind_unknown_ticker_and_no_reasons():
    txt = llm_json([
        {"ticker": "AAA", "action": "BUY_NEW", "reasons": ["x"]},          # candidate action on a holding
        {"ticker": "CCC", "action": "BUY_MORE", "reasons": ["x"]},         # holding action on a candidate
        {"ticker": "ZZZ", "action": "HOLD", "reasons": ["x"]},             # not asked for
        {"ticker": "BBB", "action": "HOLD", "reasons": []},                # no reason
        {"ticker": "BBB", "action": "WAIT", "reasons": ["x"]},             # not in enum
    ])
    _, out = parse_verdicts(txt, ["AAA", "BBB"], ["CCC"], V)
    assert out == {}


def test_parse_drops_unknown_or_foreign_news_ids_and_clamps_confidence():
    txt = llm_json([{"ticker": "BBB", "action": "HOLD", "reasons": ["x"], "confidence": 7, "cited_news_ids": [1, 99]}])
    _, out = parse_verdicts(txt, ["BBB"], [], V)         # BBB has no news -> any id is invalid
    assert out["BBB"]["cited_news_ids"] == [] and out["BBB"]["confidence"] == 1.0


def test_parse_strips_inline_news_ids_from_text():
    txt = llm_json([{"ticker": "AAA", "action": "HOLD", "cited_news_ids": [1],
                     "reasons": ["Nội bộ bán 350.000 cp (id=1, id=2) tạo áp lực.", "(id=2)"],
                     "risks": ["Giảm lãi suất (news_id: 1) ảnh hưởng NIM"]}],
                   market="VNINDEX giảm, tin id=1 cho thấy áp lực.")
    summ, out = parse_verdicts(txt, ["AAA"], [], V)
    assert out["AAA"]["reasons"] == ["Nội bộ bán 350.000 cp tạo áp lực."]      # id-only reason dropped
    assert out["AAA"]["risks"] == ["Giảm lãi suất ảnh hưởng NIM"]
    assert summ["market"] == "VNINDEX giảm, tin cho thấy áp lực." and out["AAA"]["cited_news_ids"] == [1]


def test_parse_garbage_returns_empty():
    assert parse_verdicts("không phải json", ["AAA"], [], V) == ({}, {})
    assert parse_verdicts("{broken", ["AAA"], [], V) == ({}, {})


# ── limits ────────────────────────────────────────────────────────────────────

def test_size_lots_rounds_down_to_100_and_includes_fee():
    q = size_lots(100_000_000, 33_333.0)
    assert q % 100 == 0 and q * 33_333.0 * 1.0015 <= 100_000_000 < (q + 100) * 33_333.0 * 1.0015
    assert size_lots(0, 10_000) == 0 and size_lots(1_000_000, 0) == 0


def test_cut_loss_sells_all_sellable():
    r = apply_limits({"AAA": verdict("AAA", "CUT_LOSS")}, ev([pos("AAA", qty=1000, sellable=600)]))
    assert act(r, "AAA")["action"] == "CUT_LOSS" and act(r, "AAA")["quantity"] == 600


def test_take_profit_sells_half_in_lots():
    r = apply_limits({"AAA": verdict("AAA", "TAKE_PROFIT")}, ev([pos("AAA", qty=1000)]))
    assert act(r, "AAA")["quantity"] == 500
    r = apply_limits({"AAA": verdict("AAA", "TAKE_PROFIT")}, ev([pos("AAA", qty=300)]))
    assert act(r, "AAA")["quantity"] == 100


def test_sell_with_nothing_sellable_downgrades_to_hold():
    r = apply_limits({"AAA": verdict("AAA", "CUT_LOSS")}, ev([pos("AAA", sellable=0)]))
    a = act(r, "AAA")
    assert a["action"] == "HOLD" and a["quantity"] == 0 and "t2_not_sellable" in a["limit_codes"]


def test_buy_more_capped_by_cash_pct_and_weight_room():
    p = pos("AAA", qty=1000, price=20_000.0)                       # value 20M, NAV 1B
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE")}, ev([p]))
    assert act(r, "AAA")["quantity"] == size_lots(500_000_000 * 0.10, 20_000.0)
    big = pos("AAA", qty=12_000, price=20_000.0)                   # 240M -> only 10M room to the 25% cap
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE")}, ev([big]))
    assert act(r, "AAA")["quantity"] == size_lots(MAX_TICKER_WEIGHT * NAV - 240_000_000, 20_000.0)


def test_buy_more_blocked_at_cap_without_cash_and_when_averaging_down():
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE")}, ev([pos("AAA", qty=13_000)]))        # 260M > cap
    assert act(r, "AAA")["action"] == "HOLD" and "no_cash_or_weight_cap" in act(r, "AAA")["limit_codes"]
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE")}, ev([pos("AAA")], cash=0.0))
    assert act(r, "AAA")["action"] == "HOLD"
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE")}, ev([pos("AAA", pnl=-9)]))
    assert act(r, "AAA")["action"] == "HOLD" and "averaging_down_blocked" in act(r, "AAA")["limit_codes"]


def test_missing_verdict_is_hold_invalid_output():
    r = apply_limits({}, ev([pos("AAA")]))
    assert act(r, "AAA")["action"] == "HOLD" and act(r, "AAA")["limit_codes"] == ["invalid_llm_output"]


def test_candidates_sized_skip_ignored_and_cash_consumed_across_buys():
    cash = 100_000_000.0
    e = ev([pos("AAA")], [cand("CCC"), cand("DDD"), cand("EEE")], cash=cash)
    v = {"AAA": verdict("AAA", "BUY_MORE"), "CCC": verdict("CCC", "BUY_NEW"), "DDD": verdict("DDD", "SKIP"),
         "EEE": verdict("EEE", "BUY_NEW")}
    r = apply_limits(v, e)
    assert [i["ticker"] for i in r["new_ideas"]] == ["CCC", "EEE"]
    spent = (act(r, "AAA")["quantity"] * 20_000.0 + sum(i["quantity"] * i["price_ref"] for i in r["new_ideas"])) * 1.0015
    assert spent <= cash and all(i["quantity"] % 100 == 0 for i in r["new_ideas"])


def test_candidate_dropped_when_cash_too_small():
    r = apply_limits({"CCC": verdict("CCC", "BUY_NEW")}, ev([], [cand("CCC", price=500_000.0)], cash=1_000_000.0))
    assert r["new_ideas"] == []


# ── stop / target levels ──────────────────────────────────────────────────────

def test_parse_levels_only_for_buy_actions():
    txt = llm_json([
        {"ticker": "AAA", "action": "BUY_MORE", "reasons": ["x"], "stop_price": "18,500", "target_price": 24000,
         "levels_basis": "hỗ trợ 18,500 (id=1), kháng cự 24,000"},
        {"ticker": "BBB", "action": "HOLD", "reasons": ["x"], "stop_price": 15000, "target_price": 30000},
        {"ticker": "CCC", "action": "BUY_NEW", "reasons": ["x"], "stop_price": "n/a", "target_price": None},
    ])
    _, out = parse_verdicts(txt, ["AAA", "BBB"], ["CCC"], V)
    assert (out["AAA"]["stop_price"], out["AAA"]["target_price"]) == (18_500.0, 24_000.0)
    assert out["AAA"]["levels_basis"] == "hỗ trợ 18,500, kháng cự 24,000"
    assert out["BBB"]["stop_price"] is None and out["BBB"]["levels_basis"] is None
    assert out["CCC"]["stop_price"] is None and out["CCC"]["target_price"] is None


def test_valid_ai_levels_are_kept_on_buys():
    v = {"AAA": verdict("AAA", "BUY_MORE", stop_price=18_500.0, target_price=24_000.0, levels_basis="hỗ trợ"),
         "CCC": verdict("CCC", "BUY_NEW", stop_price=27_000.0, target_price=36_000.0, levels_basis="MA50")}
    r = apply_limits(v, ev([pos("AAA")], [cand("CCC")]))
    a, i = act(r, "AAA"), r["new_ideas"][0]
    assert (a["stop_price"], a["target_price"], a["levels_source"]) == (18_500.0, 24_000.0, "ai")
    assert (i["stop_price"], i["target_price"], i["levels_source"]) == (27_000.0, 36_000.0, "ai")


@pytest.mark.parametrize("stop", [None, 19_800.0, 20_000.0, 13_000.0, 18.5])   # missing, <2%, at price, >30%, unit
def test_invalid_ai_stop_falls_back_to_default(stop):
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE", stop_price=stop, target_price=24_000.0)}, ev([pos("AAA")]))
    a = act(r, "AAA")
    assert a["stop_price"] == 18_600 and a["levels_source"] == "default" and a["target_price"] == 24_000.0
    assert a["levels_basis"] is None                                 # the AI basis described the rejected stop


@pytest.mark.parametrize("target", [19_000.0, 20_000.0, 50_000.0, 24.0])
def test_invalid_ai_target_is_dropped(target):
    r = apply_limits({"AAA": verdict("AAA", "BUY_MORE", stop_price=18_500.0, target_price=target)}, ev([pos("AAA")]))
    assert act(r, "AAA")["target_price"] is None and act(r, "AAA")["stop_price"] == 18_500.0


def test_non_buy_actions_and_downgraded_buys_have_no_levels():
    r = apply_limits({"AAA": verdict("AAA", "HOLD", stop_price=18_500.0),
                      "BBB": verdict("BBB", "BUY_MORE", stop_price=18_500.0)},
                     ev([pos("AAA"), pos("BBB", pnl=-9)]))
    assert "stop_price" not in act(r, "AAA") and "stop_price" not in act(r, "BBB")


# ── advise (LLM stubbed) ──────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


def _evidence():
    ps = [pos("AAA", pnl=-10, sector="Bank"), pos("BBB", pnl=3, sector="Steel")]
    cash = {"cash": 500_000_000.0, "nav": NAV, "total_return_pct": 1.5}
    news = {"AAA": [{"id": 7, "url": "http://n/7", "title": "AAA lỗ quý", "source": "cafef", "published_at": "2026-10-18", "snippet": "s"}],
            "BBB": [], "CCC": []}
    return {"user_id": "u1", "as_of": "2026-10-19", "positions": ps, "cash": cash, "candidates": [cand("CCC")],
            "market": "[THỊ TRƯỜNG] x", "market_news": [], "technical": {"AAA": "ta", "BBB": "tb", "CCC": "tc"},
            "fundamentals": {"AAA": "fa"}, "signals": {"CCC": "sc"},
            "news": news, "foreign_flows": {"AAA": {"net_5d_bn": -3.0, "net_20d_bn": -9.0}},
            "risk": compute_risk(ps, cash, {})}


GOOD = llm_json([
    {"ticker": "AAA", "action": "CUT_LOSS", "confidence": 0.8, "reasons": ["Lỗ 10%, khối ngoại bán ròng 9 tỷ"], "cited_news_ids": [7, 123]},
    {"ticker": "BBB", "action": "HOLD", "confidence": 0.6, "reasons": ["Lãi 3%, chưa có tín hiệu rõ"], "cited_news_ids": [5]},
    {"ticker": "CCC", "action": "BUY_NEW", "confidence": 0.5, "reasons": ["Tăng 5%/20 phiên, khối ngoại mua ròng"]},
], market="VNINDEX giữ đà tăng.", ideas_summary="CCC thanh khoản tốt.", risk_summary="Tiền mặt cao.")


def _run(monkeypatch, *texts):
    calls = []

    class Client:
        def generate(self, *a, **k):
            calls.append(1)
            return _Resp(texts[min(len(calls), len(texts)) - 1])

    monkeypatch.setattr(advisor, "gather_evidence_for", lambda ps, cash, uid: _evidence())
    monkeypatch.setattr("llm.create_client", lambda: Client())
    return advisor.advise_for([], {"cash": 0}, "u1"), calls


def test_advise_report_structure_and_validated_output(monkeypatch):
    out, calls = _run(monkeypatch, GOOD)
    md = out["report_md"]
    for h in ("## 1. Bối cảnh thị trường", "## 2. Hành động theo từng mã", "## 3. Ý tưởng mua mới", "## 4. Rủi ro danh mục"):
        assert h in md
    assert "VNINDEX giữ đà tăng." in md and "[AAA lỗ quý](http://n/7)" in md
    assert [a["action"] for a in out["actions"]] == ["CUT_LOSS", "HOLD"] and out["actions"][0]["quantity"] == 1000
    assert out["actions"][0]["cited_news"][0]["id"] == 7 and len(out["actions"][0]["cited_news"]) == 1   # id 123 dropped
    assert out["actions"][1]["cited_news"] == []                                                          # BBB has no news
    assert out["new_ideas"][0]["ticker"] == "CCC" and out["llm_missing"] == [] and len(calls) == 1


def test_retry_once_when_a_ticker_is_missing_then_succeeds(monkeypatch):
    partial = llm_json([json.loads(GOOD)["verdicts"][0]])
    out, calls = _run(monkeypatch, partial, GOOD)
    assert len(calls) == 2 and out["llm_missing"] == []


def test_still_missing_after_retry_falls_back_to_hold(monkeypatch):
    partial = llm_json([json.loads(GOOD)["verdicts"][0]])
    out, calls = _run(monkeypatch, partial)
    assert len(calls) == 2 and set(out["llm_missing"]) == {"BBB", "CCC"}
    assert out["actions"][1]["action"] == "HOLD" and out["actions"][1]["limit_codes"] == ["invalid_llm_output"]


def test_no_parsable_json_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _run(monkeypatch, "xin lỗi, không thể")


def test_disclaimer_present(monkeypatch):
    out, _ = _run(monkeypatch, GOOD)
    assert "tham khảo" in out["report_md"].lower() or "không phải lời khuyên" in out["report_md"].lower()


def test_prompt_includes_fundamentals_and_breakout_per_ticker():
    p = advisor._prompt(_evidence())
    held, cands = p.split("[MÃ ỨNG VIÊN MUA MỚI]")
    assert "fa" in held.split("- BBB")[0] and "sc" in cands
