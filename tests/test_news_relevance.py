"""Unit tests for portfolio/news_relevance.py: ticker / sector / related news groups (no DB). Titles are real ones
from news_articles (10/2026)."""
from __future__ import annotations

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
