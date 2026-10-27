"""Unit tests for portfolio/news_relevance.py (ticker / sector / related groups) and news_scorer (no DB, fake LLM). Titles are real ones
from news_articles (10/2026)."""
from __future__ import annotations

import json

from portfolio import news_scorer
from portfolio.news_relevance import select_news

META = {
    "DCM": {"sector": "Hóa chất", "tags": ["phân bón", "sx nhựa - hóa chất"], "aliases": ["đạm cà mau"]},
    "TCB": {"sector": "Ngân hàng", "tags": ["ngân hàng"], "aliases": ["techcombank"]},
    "MSN": {"sector": "Thực phẩm & Đồ uống", "tags": ["thực phẩm"], "aliases": ["masan"]},
    "HPG": {"sector": "Vật liệu", "tags": ["thép", "vật liệu xây dựng"], "aliases": ["hòa phát"]},
}


def art(id_, title, tickers=()):
    return {"id": id_, "title": title, "tickers": list(tickers)}


def groups(arts, tickers=None):
    return select_news(arts, {t: META[t] for t in tickers or META}, per_ticker=8)


def ids(g):
    return [a["id"] for a in g]


def test_tagged_news_stays_ticker_news_and_untagged_title_naming_the_company_is_added():
    out = groups([art(1, "Phân bón giả ngày càng tinh vi", ["DCM"]),
                  art(2, "Đạm Cà Mau vượt kế hoạch xuất khẩu"),
                  art(3, "MSR: Lợi nhuận 9 tháng vượt 74% kế hoạch")])          # subsidiary of MSN
    assert ids(out["DCM"]["ticker"]) == [1, 2] and ids(out["MSN"]["ticker"]) == [3]


def test_sector_news_for_the_tickers_own_industry():
    out = groups([art(1, "Nguồn thu ngoài lãi tiếp sức cho lợi nhuận ngân hàng"),
                  art(2, "'Quả đấm thép' một thời ngành phân bón có quý thứ 3 lãi trên trăm tỷ")])
    assert ids(out["TCB"]["sector"]) == [1] and out["TCB"]["sector"][0]["kind"] == "sector"
    assert ids(out["DCM"]["sector"]) == [2] and out["HPG"]["sector"] == []        # a metaphor, not steel


def test_related_news_carries_its_channel():
    out = groups([art(1, "Một số ngân hàng có thể đã hết room tín dụng sau quy định mới của Ngân hàng Nhà nước"),
                  art(2, "Giải ngân vốn đầu tư công chạy đua về đích cuối năm"),
                  art(3, "DBC: Giá lợn hơi giảm, Dabaco báo lãi ròng quý III giảm")])
    assert ids(out["HPG"]["related"]) == [2] and "thép" in out["HPG"]["related"][0]["group"]
    assert ids(out["MSN"]["related"]) == [3] and out["DCM"]["related"] == []
    assert 1 in ids(out["TCB"]["sector"] + out["TCB"]["related"])


def test_false_friends_listicles_and_other_companies_filings_are_dropped():
    out = groups([art(1, "Thị trường ảm đạm, công ty chứng khoán làm ăn ra sao?"),
                  art(2, "Tái thiết Vĩnh Hưng: chuyển hóa chất lượng sống người dân"),
                  art(3, "Lãi suất BIDV mới nhất, gửi 500 triệu đồng nhận 82,5 triệu đồng tiền lãi"),
                  art(4, "TPB: Thông báo quyết định của NHNN về việc sửa đổi vốn điều lệ"),
                  art(5, "NKG: Thép Nam Kim chốt quyền tham dự ĐHĐCĐ bất thường")])
    assert all(not g["sector"] and not g["related"] for g in out.values())


def test_one_story_once_per_ticker_and_caps():
    arts = [art(1, "QNS: Chủ hãng sữa Fami lãi ròng gần 450 tỷ"), art(2, "Chủ hãng sữa Fami lãi ròng gần 450 tỷ")]
    arts += [art(10 + i, f"Ngân hàng {i} tăng vốn") for i in range(6)]
    out = groups(arts, ["MSN", "TCB"])
    assert ids(out["MSN"]["sector"]) == [1] and len(out["TCB"]["sector"]) == 4


def test_a_ticker_with_its_own_channels_uses_them_instead_of_its_sectors():
    out = groups([art(1, "Giá vonfram lập đỉnh mới"),                              # MSR → MSN only via MSN's channels
                  art(2, "Thị trường ô tô tháng 10 tăng mạnh"),                    # VIB-specific, not TCB
                  art(3, "Ai sẽ thắng cuộc đua thị phần?")],                       # "ai" is not "AI"
                 ["MSN", "TCB"])
    assert ids(out["MSN"]["related"]) == [1] and "vonfram" in out["MSN"]["related"][0]["group"]
    assert out["TCB"]["related"] == []
    vib = select_news([art(2, "Thị trường ô tô tháng 10 tăng mạnh")],
                      {"VIB": {"sector": "Ngân hàng", "tags": ["ngân hàng"], "aliases": []}}, per_ticker=8)
    assert ids(vib["VIB"]["related"]) == [2]
    fpt = select_news([art(3, "Ai sẽ thắng cuộc đua thị phần?"), art(4, "Nhu cầu AI bùng nổ")],
                      {"FPT": {"sector": "Công nghệ", "tags": [], "aliases": []}}, per_ticker=8)
    assert ids(fpt["FPT"]["related"]) == [4]


# ── news_scorer ──────────────────────────────────────────────────────────────────────────────────────────────────

def pool():
    a = lambda i, kind, group="g": {"id": i, "title": f"t{i}", "snippet": "s", "published_at": "2026-10-27",
                                    "kind": kind, "group": group}
    return {"DCM": {"ticker": [{"id": 1, "title": "DCM lãi"}],
                    "sector": [a(10, "sector"), a(11, "sector"), a(12, "sector")],
                    "related": [a(20, "related", "khí"), a(21, "related", "khí"), a(22, "related", "khí")]}}


class FakeClient:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def generate(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.exc:
            raise self.exc
        return type("R", (), {"text": self.text})()


def scored(items):
    return json.dumps({"items": [{"t": "DCM", "id": i, "score": s, "impact": f"i{i}"} for i, s in items]})


def test_scorer_keeps_material_articles_best_first_and_never_touches_ticker_news():
    c = FakeClient(scored([(10, 1), (11, 3), (12, 2), (20, 0), (21, 2), (22, 3), (99, 3)]))
    out, st = news_scorer.score_news(pool(), {"DCM": {"sector": "Hóa chất", "tags": ["phân bón"]}}, client=c)
    assert [a["id"] for a in out["DCM"]["sector"]] == [11, 12] and out["DCM"]["sector"][0]["impact"] == "i11"
    assert [a["id"] for a in out["DCM"]["related"]] == [22, 21] and out["DCM"]["ticker"] == [{"id": 1, "title": "DCM lãi"}]
    assert st == {"scored": True, "pairs": 6, "kept": 4, "error": None}
    prompt = c.calls[0][0][0].content
    assert "Kênh tác động: giá khí đầu vào" in prompt and "id=20 [kênh: khí]" in prompt and c.calls[0][1]["temperature"] == 0


def test_scorer_falls_back_to_the_keyword_cut_on_error_or_unusable_output():
    for c in (FakeClient(exc=RuntimeError("down")), FakeClient("không phải json"), FakeClient(scored([(10, 3)]))):
        out, st = news_scorer.score_news(pool(), {"DCM": {}}, client=c)
        assert st["scored"] is False and st["error"]
        assert [a["id"] for a in out["DCM"]["sector"]] == [10, 11, 12]
        assert [a["id"] for a in out["DCM"]["related"]] == [20, 21]               # 2 per channel
