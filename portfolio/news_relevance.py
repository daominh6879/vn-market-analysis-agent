"""
portfolio/news_relevance.py — the three news groups the advisor judges a ticker on (pure, no DB).

- ticker: articles tagged with the ticker (the per-ticker FireAnt/CafeF feeds), plus untagged ones naming the company,
  one of its `securities.name_aliases` or a related company (RELATED: subsidiary / parent) in the title;
- sector: industry-wide news for the ticker's own sector (SECTOR_TERMS on the title, keyed by sector / tags / code);
- related: news from other sectors or macro that moves the ticker through a named channel (gas price -> fertiliser
  cost, NHNN credit room -> bank lending, Brent -> jet fuel), matched by DRIVERS; each carries its channel label.

An article lands in one group per ticker (ticker first, then sector, then related), one story (same title) once.
Personal-finance and price-of-the-day listicles are never sector / related news.
"""
from __future__ import annotations

import re

SECTOR_NEWS_PER_TICKER = 4
RELATED_NEWS_PER_TICKER = 4
RELATED_NEWS_PER_CHANNEL = 2

# Subsidiaries / parents whose news is the company's news (facts, not views). Extend as holdings change.
RELATED: dict[str, tuple[str, ...]] = {
    "MSN": ("MSR", "MCH", "Masan High-Tech", "Masan Consumer", "Masan MEATLife", "MML", "WinCommerce", "WinMart",
            "Phúc Long"),
    "VIC": ("VHM", "VRE", "Vinhomes", "VinFast", "Vincom Retail"),
    "FPT": ("FPT Telecom", "FPT Retail", "FRT", "FPT Software"),
    "HPG": ("Hòa Phát Dung Quất",),
}

LISTICLE_RE = re.compile(
    r"gửi (tiết kiệm|\d)|nhận (lãi|.* tiền lãi)|lãi suất .*(hôm nay|mới nhất|ngày \d)|cập nhật lãi suất|"
    r"giá (vàng|xăng|heo|lợn|cà phê|thép|cao su|tiêu) hôm nay|bảng giá .* hôm nay", re.IGNORECASE)

# "XXX: ..." — another listed company's own story; its routine filings are noise for this ticker.
_CODE_PREFIX_RE = re.compile(r"^\s*[A-Z0-9]{3}\s*:\s*")
_FILING_RE = re.compile(r"^\s*[A-Z0-9]{3}\s*:\s*(.*(chốt quyền|đhđcđ|đại hội đồng cổ đông|giao dịch cổ phiếu của)|"
                        r"thông báo|nghị quyết|giải trình|báo cáo|quyết định|cbtt|công bố thông tin)", re.IGNORECASE)


def _compile(rows) -> tuple[tuple[str, frozenset[str], re.Pattern], ...]:
    return tuple((label, frozenset(k.lower() for k in keys), re.compile(p, re.IGNORECASE)) for label, keys, p in rows)


# (sector label, sectors / tags / tickers it applies to, title pattern) — the ticker's own industry.
SECTOR_TERMS = _compile((
    ("ngân hàng", ("ngân hàng",), r"ngân hàng|nhà băng|\bnim\b|nợ xấu|tín dụng"),
    ("chứng khoán", ("chứng khoán",), r"công ty chứng khoán|\bctck\b|môi giới chứng khoán|cho vay ký quỹ"),
    ("bất động sản", ("bất động sản",), r"bất động sản|\bbđs\b|địa ốc|dự án nhà ở|chung cư|khu công nghiệp"),
    ("thép", ("thép", "HPG", "HSG", "NKG"), r"(?<!đấm )\bthép\b"),
    ("vật liệu xây dựng", ("vật liệu xây dựng", "xi măng"), r"vật liệu xây dựng|xi măng|\bđá xây dựng\b"),
    ("phân bón, hóa chất", ("phân bón", "hóa chất", "sx nhựa - hóa chất", "DCM", "DPM"),
     r"phân bón|\bur[eê]\b|phân đạm|hóa chất(?! lượng)"),
    ("dầu khí", ("dầu thô", "dầu khí"), r"dầu khí|khai thác dầu|lọc dầu|xăng dầu"),
    ("điện", ("điện", "thủy điện"), r"ngành điện|nhà máy điện|thủy điện|nhiệt điện|điện gió|điện mặt trời|\bevn\b"),
    ("thực phẩm, đồ uống", ("thực phẩm", "đồ uống", "thực phẩm - đồ uống", "sữa"),
     r"thực phẩm|đồ uống|\bbia\b|\bsữa\b|hàng tiêu dùng nhanh|\bfmcg\b"),
    ("bán lẻ", ("bán lẻ", "trung tâm thương mại"), r"bán lẻ|siêu thị|chuỗi cửa hàng"),
    ("vận tải, cảng, logistics", ("vận tải", "vận tải - kho bãi", "logistics"), r"cảng biển|logistics|vận tải|kho bãi"),
    ("hàng không", ("hàng không", "HVN", "VJC", "ACV"), r"hàng không|hãng bay|sân bay"),
    ("xây dựng", ("xây dựng",), r"nhà thầu|xây lắp|ngành xây dựng"),
    ("dệt may", ("dệt may",), r"dệt may|may mặc"),
    ("thủy sản", ("thủy sản", "chế biến thủy sản"), r"thủy sản|\btôm\b|cá tra"),
    ("công nghệ", ("công nghệ và thông tin", "công nghệ"), r"công nghệ thông tin|phần mềm|bán dẫn|trung tâm dữ liệu|xuất khẩu phần mềm"),
    ("bảo hiểm", ("bảo hiểm",), r"bảo hiểm"),
    ("y tế, dược", ("chăm sóc sức khỏe", "y tế"), r"dược phẩm|ngành dược|thuốc|bệnh viện"),
    ("cao su", ("cao su", "sản phẩm cao su"), r"cao su"),
    ("nông nghiệp", ("nông - lâm - ngư", "nông nghiệp"), r"nông sản|chăn nuôi|cà phê|lúa gạo"),
))

# (channel label, sectors / tags / tickers it applies to, title pattern) — another sector or macro moving the ticker.
DRIVERS = _compile((
    ("chính sách tiền tệ, tín dụng (NHNN) → tăng trưởng cho vay, NIM, nợ xấu",
     ("ngân hàng", "chứng khoán", "bất động sản", "công ty tài chính"),
     r"lãi suất điều hành|ngân hàng nhà nước|\bnhnn\b|room tín dụng|hạn mức tín dụng|bơm ròng|hút ròng|tín phiếu"),
    ("Fed, tỷ giá USD/VND → dòng vốn ngoại, chi phí vốn", ("ngân hàng", "chứng khoán", "bất động sản"),
     r"\bfed\b|tỷ giá|chỉ số usd|\bdxy\b"),
    ("nâng hạng thị trường, thanh khoản → phí môi giới, margin", ("chứng khoán",),
     r"nâng hạng|\bftse\b|\bmsci\b|\bkrx\b"),
    ("lãi suất cho vay, tín dụng, pháp lý bất động sản → nhu cầu mua nhà, doanh số", ("bất động sản",),
     r"lãi suất cho vay|tín dụng bất động sản|luật đất đai|bảng giá đất|trái phiếu doanh nghiệp|gỡ vướng pháp lý"),
    ("đầu tư công, bất động sản → nhu cầu thép, xi măng, xây lắp",
     ("xây dựng", "vật liệu xây dựng", "thép", "xi măng"),
     r"đầu tư công|giải ngân vốn|cao tốc|đường sắt tốc độ cao|thị trường bất động sản"),
    ("quặng sắt, than cốc, thép Trung Quốc → chi phí đầu vào, giá bán", ("thép", "HPG", "HSG", "NKG"),
     r"quặng sắt|than cốc|thép trung quốc|bán phá giá"),
    ("giá khí, giá dầu → chi phí khí đầu vào, giá urê", ("phân bón", "DCM", "DPM"),
     r"giá khí|khí đầu vào|giá dầu|\bbrent\b|\blng\b"),
    ("giá dầu thế giới → giá bán, sản lượng dầu khí", ("dầu thô", "dầu khí", "GAS", "PLX", "PVS", "PVD", "BSR"),
     r"giá dầu|\bbrent\b|\bwti\b|\bopec\b"),
    ("giá dầu, du lịch → chi phí nhiên liệu bay, lượng khách", ("hàng không", "HVN", "VJC", "ACV"),
     r"giá dầu|nhiên liệu bay|jet ?a-?1|khách quốc tế|du lịch"),
    ("thời tiết, giá khí/than → sản lượng và giá bán điện", ("điện", "thủy điện"),
     r"thủy văn|el ?nino|la ?nina|giá than|giá khí|quy hoạch điện"),
    ("thương mại toàn cầu → giá cước, sản lượng hàng qua cảng", ("vận tải", "vận tải - kho bãi", "logistics"),
     r"giá cước|cước container|biển đỏ|\bscfi\b|xuất nhập khẩu|thuế đối ứng"),
    ("thuế Mỹ, chống bán phá giá → đơn hàng xuất khẩu",
     ("dệt may", "thủy sản", "chế biến thủy sản", "sản phẩm cao su", "thép"),
     r"thuế đối ứng|thuế quan|chống bán phá giá|xuất khẩu sang mỹ"),
    ("giá heo, ngô, đậu tương → giá bán và chi phí chăn nuôi", ("MSN", "MML", "DBC", "HAG", "BAF", "nông - lâm - ngư"),
     r"giá (heo|lợn) hơi|dịch tả|thức ăn chăn nuôi|giá ngô|đậu tương"),
    ("sức mua, thuế tiêu dùng → doanh thu bán lẻ, hàng tiêu dùng", ("bán lẻ", "thực phẩm", "đồ uống", "tiêu dùng"),
     r"sức mua|tổng mức bán lẻ|tiêu dùng nội địa|giảm thuế (vat|gtgt)|chỉ số giá tiêu dùng|\bcpi\b"),
    ("giá vàng → giá bán, nhu cầu trang sức", ("trang sức", "vàng bạc", "PNJ"), r"giá vàng"),
))


def _keys(ticker: str, sector: str | None, tags: list[str]) -> set[str]:
    return {ticker.lower(), (sector or "").lower(), *(t.lower() for t in tags or [])}


def _codes(ticker: str) -> list[str]:
    return [ticker] + [r for r in RELATED.get(ticker, ()) if re.fullmatch(r"[A-Z0-9]{3}", r)]


def _names(ticker: str, aliases: list[str]) -> list[str]:
    out = [a.strip(" -–").lower() for a in aliases or []] + [r.lower() for r in RELATED.get(ticker, ())]
    return [n for n in dict.fromkeys(out) if len(n) >= 4]


def is_ticker_news(ticker: str, aliases: list[str], title: str, tags: list[str]) -> bool:
    """Tagged with the ticker or a related code, or the title names the company / a related company."""
    codes = _codes(ticker)
    if any(c in (tags or []) for c in codes):
        return True
    tl = title.lower()
    return any(re.search(rf"\b{c}\b", title) for c in codes) or any(n in tl for n in _names(ticker, aliases))


def match(table, ticker: str, sector: str | None, tags: list[str], title: str) -> str | None:
    """Label of the first SECTOR_TERMS / DRIVERS row that applies to the ticker and matches the title."""
    if LISTICLE_RE.search(title) or _FILING_RE.match(title):
        return None
    keys = _keys(ticker, sector, tags)
    return next((label for label, applies, pat in table if applies & keys and pat.search(title)), None)


def select_news(articles: list[dict], meta: dict[str, dict], per_ticker: int,
                sector_per_ticker: int = SECTOR_NEWS_PER_TICKER,
                related_per_ticker: int = RELATED_NEWS_PER_TICKER) -> dict[str, dict[str, list[dict]]]:
    """articles newest first, each {id, title, tickers, ...}; meta = {ticker: {sector, tags, aliases}}.
    Returns {ticker: {"ticker": [...], "sector": [... with `group` label], "related": [... with `group` channel]}};
    every ticker has all three keys. RELATED_NEWS_PER_CHANNEL per channel so one driver does not fill the group."""
    out = {t: {"ticker": [], "sector": [], "related": []} for t in meta}
    seen: dict[str, set[str]] = {t: set() for t in meta}
    for a in articles:
        key = " ".join(_CODE_PREFIX_RE.sub("", a["title"]).lower().split())
        for t, m in meta.items():
            if key in seen[t]:
                continue
            g, sector, tags = out[t], m.get("sector"), m.get("tags") or []
            if is_ticker_news(t, m.get("aliases") or [], a["title"], a.get("tickers") or []):
                if len(g["ticker"]) < per_ticker:
                    g["ticker"].append(a)
                    seen[t].add(key)
                continue
            if len(g["sector"]) < sector_per_ticker and (label := match(SECTOR_TERMS, t, sector, tags, a["title"])):
                g["sector"].append({**a, "kind": "sector", "group": label})
                seen[t].add(key)
                continue
            if len(g["related"]) < related_per_ticker and (label := match(DRIVERS, t, sector, tags, a["title"])) \
                    and sum(d["group"] == label for d in g["related"]) < RELATED_NEWS_PER_CHANNEL:
                g["related"].append({**a, "kind": "related", "group": label})
                seen[t].add(key)
    return out
