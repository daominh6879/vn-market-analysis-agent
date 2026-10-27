"""
portfolio/news_relevance.py — the three news groups the advisor judges a ticker on (pure, no DB).

- ticker: articles tagged with the ticker (the per-ticker FireAnt/CafeF feeds), plus untagged ones naming the company,
  one of its `securities.name_aliases` or a related company (RELATED: subsidiary / parent) in the title;
- sector: industry-wide news for the ticker's own sector (SECTOR_TERMS on the title, keyed by sector / tags / code);
- related: news from other sectors or macro that moves the ticker through a named channel (gas price -> fertiliser
  cost, NHNN credit room -> bank lending, Brent -> jet fuel), matched on the company's own TICKER_CHANNELS when it has
  them, else on its sector's DRIVERS; each carries its channel label.

The keyword match is a wide pre-filter (POOL_PER_GROUP); news_scorer then has the LLM keep the articles that actually
move the company.

An article lands in one group per ticker (ticker first, then sector, then related), one story (same title) once.
Personal-finance and price-of-the-day listicles are never sector / related news.
"""
from __future__ import annotations

import re

SECTOR_NEWS_PER_TICKER = 4
RELATED_NEWS_PER_TICKER = 4
RELATED_NEWS_PER_CHANNEL = 2
POOL_PER_GROUP = 10              # keyword-matched candidates per group handed to news_scorer, which keeps the best 4
POOL_PER_CHANNEL = 4

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

# Per-company channels: how this business is actually moved (its inputs, prices, demand, policy, stakes). A ticker
# listed here uses these instead of its sector's DRIVERS; others fall back to DRIVERS. Keep to business facts.
_NHNN = r"ngân hàng nhà nước|\bnhnn\b|room tín dụng|hạn mức tín dụng|tăng trưởng tín dụng|lãi suất điều hành|bơm ròng|hút ròng|tín phiếu"
_FX = r"\bfed\b|tỷ giá|chỉ số usd|\bdxy\b"
_HOUSING = r"thị trường bất động sản|tín dụng bất động sản|cho vay mua nhà|trái phiếu doanh nghiệp|luật đất đai|bảng giá đất|gỡ vướng pháp lý"
_SPENDING = r"sức mua|tổng mức bán lẻ|tiêu dùng nội địa|giảm thuế (vat|gtgt)|chỉ số giá tiêu dùng|\bcpi\b"
_OIL = r"giá dầu|\bbrent\b|\bwti\b|\bopec\b"
_UREA = r"\bur[eê]\b|\burea\b|xuất khẩu phân bón|giá phân bón|thuế.*phân bón|phân bón.*thuế"
_STEEL_IN = r"quặng sắt|than cốc|thép trung quốc|giá thép thế giới|thép cán nóng|\bhrc\b"
_TRADE = r"chống bán phá giá|thuế đối ứng|thuế quan|xuất khẩu sang mỹ"
_PUBLIC = r"đầu tư công|giải ngân vốn|cao tốc|đường sắt tốc độ cao"
_BROKER = r"nâng hạng|\bftse\b|\bmsci\b|\bkrx\b|thanh khoản thị trường|cho vay ký quỹ|\bmargin\b"
_POWER = r"giá điện|\bevn\b|quy hoạch điện|thủy văn|el ?nino|la ?nina|giá than"
_BANK = (("chính sách tiền tệ, tín dụng (NHNN) → tăng trưởng cho vay, NIM", _NHNN),
         ("Fed, tỷ giá → chi phí vốn, dòng vốn ngoại", _FX))
_BROKERS = (("nâng hạng, thanh khoản, margin → phí môi giới, lãi cho vay ký quỹ", _BROKER),
            ("thanh khoản hệ thống (NHNN) → chi phí vốn margin", _NHNN))
_DEVELOPER = (("tín dụng, pháp lý bất động sản → mở bán, ghi nhận doanh thu", _HOUSING),
              ("lãi suất (NHNN) → nhu cầu vay mua nhà", _NHNN), ("hạ tầng, đầu tư công → giá đất dự án", _PUBLIC))
_FERTILISER = (("giá khí đầu vào, giá dầu → chi phí sản xuất urê", r"giá khí|khí đầu vào|\blng\b|" + _OIL),
               ("giá urê thế giới, thuế xuất khẩu / VAT phân bón → giá bán, biên lợi nhuận", _UREA),
               ("mùa vụ, giá lúa gạo → nhu cầu phân bón trong nước", r"giá lúa|giá gạo|vụ đông xuân|vụ hè thu|mùa vụ"))
_GALVANISED = (("giá thép cán nóng (HRC), quặng → chi phí đầu vào tôn", _STEEL_IN),
               ("thuế chống bán phá giá, thuế Mỹ/EU → xuất khẩu tôn", _TRADE),
               ("bất động sản dân dụng → nhu cầu tôn trong nước", _HOUSING))
_PORTS = (("thương mại, cước container → sản lượng hàng qua cảng, giá cước",
           r"giá cước|cước container|biển đỏ|\bscfi\b|xuất nhập khẩu|kim ngạch"), ("thuế Mỹ → hàng xuất khẩu", _TRADE))
_PANGASIUS = (("xuất khẩu cá tra, thuế chống bán phá giá Mỹ → giá bán, đơn hàng", r"cá tra|xuất khẩu thủy sản|" + _TRADE),
              ("tỷ giá USD → doanh thu xuất khẩu quy đổi", _FX))
_POWER_GEN = (("giá điện, thủy văn, quy hoạch điện → sản lượng, giá bán điện", _POWER),)
TICKER_CHANNELS: dict[str, tuple[tuple[str, re.Pattern], ...]] = {t: tuple((l, re.compile(p, re.IGNORECASE)) for l, p in rows)
                                                                  for t, rows in {
    "DCM": _FERTILISER, "DPM": _FERTILISER,
    "MSN": (("giá heo hơi, thức ăn chăn nuôi → mảng thịt (Masan MEATLife)",
             r"giá (heo|lợn) hơi|dịch tả|thức ăn chăn nuôi|giá ngô|đậu tương"),
            ("sức mua, thuế tiêu dùng → hàng tiêu dùng (MCH), bán lẻ (WinCommerce)", _SPENDING),
            ("giá vonfram → Masan High-Tech Materials (MSR)", r"vonfram|tungsten"),
            ("Techcombank (công ty liên kết) → lãi từ công ty liên kết", r"techcombank|\bTCB\b")),
    "HPG": (("quặng sắt, than cốc, giá thép Trung Quốc → chi phí và giá bán thép", _STEEL_IN),
            ("đầu tư công → nhu cầu thép xây dựng", _PUBLIC), ("bất động sản → nhu cầu thép xây dựng", _HOUSING),
            ("chống bán phá giá, thuế Mỹ → xuất khẩu và bảo hộ thép trong nước", _TRADE)),
    "HSG": _GALVANISED, "NKG": _GALVANISED,
    "TCB": _BANK + (("bất động sản, trái phiếu doanh nghiệp → cho vay BĐS, chất lượng tài sản", _HOUSING),),
    "VPB": _BANK + (("sức mua, tín dụng tiêu dùng → FE Credit, nợ xấu tiêu dùng", r"tín dụng tiêu dùng|công ty tài chính|" + _SPENDING),),
    "VIB": _BANK + (("cho vay mua nhà, mua ô tô → tăng trưởng tín dụng bán lẻ", r"cho vay mua nhà|thị trường ô tô|doanh số ô tô|" + _HOUSING),),
    "MBB": _BANK + (("bất động sản → cho vay BĐS, chất lượng tài sản", _HOUSING),),
    "ACB": _BANK + (("cho vay cá nhân, SME → tín dụng bán lẻ", r"tín dụng tiêu dùng|doanh nghiệp nhỏ và vừa|\bsme\b"),),
    "VCB": _BANK + (("cho vay ưu đãi theo chỉ đạo → NIM ngân hàng quốc doanh", r"cho vay ưu đãi|giảm lãi suất cho vay|gói tín dụng"),),
    "BID": _BANK + (("cho vay ưu đãi theo chỉ đạo → NIM ngân hàng quốc doanh", r"cho vay ưu đãi|giảm lãi suất cho vay|gói tín dụng"),),
    "CTG": _BANK + (("cho vay ưu đãi theo chỉ đạo → NIM ngân hàng quốc doanh", r"cho vay ưu đãi|giảm lãi suất cho vay|gói tín dụng"),),
    "SSI": _BROKERS, "VND": _BROKERS, "HCM": _BROKERS, "VCI": _BROKERS, "SHS": _BROKERS,
    "FPT": (("chi tiêu công nghệ Mỹ / Nhật, tỷ giá JPY, USD → xuất khẩu phần mềm",
             r"xuất khẩu phần mềm|chi tiêu công nghệ|đồng yên|\bjpy\b|" + _FX),
            ("AI, bán dẫn, trung tâm dữ liệu → mảng công nghệ mới", r"(?-i:\bAI\b)|trí tuệ nhân tạo|bán dẫn|trung tâm dữ liệu|chip"),
            ("thuế Mỹ → khách hàng xuất khẩu dịch vụ", _TRADE)),
    "MWG": (("sức mua, thuế tiêu dùng → doanh thu điện máy, Bách Hóa Xanh", _SPENDING),
            ("giá thực phẩm tươi sống → doanh thu, biên Bách Hóa Xanh", r"giá rau|giá thực phẩm|giá (heo|lợn) hơi")),
    "VNM": (("giá sữa bột nguyên liệu → giá vốn", r"sữa bột|giá sữa|nguyên liệu sữa|\bgdt\b"),
            ("sức mua → doanh thu nội địa", _SPENDING)),
    "GAS": (("giá dầu, LNG → giá bán khí, biên lợi nhuận", _OIL + r"|\blng\b|giá khí"),
            ("nhu cầu khí cho điện, phân bón → sản lượng tiêu thụ", r"điện khí|nhiệt điện khí|quy hoạch điện")),
    "PLX": (("giá dầu, giá xăng điều hành, quỹ bình ổn → biên bán lẻ xăng dầu", _OIL + r"|giá xăng|quỹ bình ổn|điều hành giá xăng"),),
    "PVS": (("giá dầu, dự án dầu khí lớn (Lô B) → khối lượng thi công", _OIL + r"|lô b|dự án dầu khí|điện gió ngoài khơi"),),
    "PVD": (("giá dầu, giá thuê giàn khoan → hiệu suất và giá thuê giàn", _OIL + r"|giàn khoan|khoan dầu"),),
    "BSR": (("giá dầu, crack spread → biên lọc dầu", _OIL + r"|crack|lọc dầu|giá xăng"),),
    "VJC": (("giá dầu, nhiên liệu bay → chi phí bay", _OIL + r"|nhiên liệu bay|jet ?a-?1"),
            ("khách quốc tế, du lịch → doanh thu", r"khách quốc tế|lượng khách|du lịch"), ("tỷ giá USD → chi phí thuê tàu bay", _FX)),
    "HVN": (("giá dầu, nhiên liệu bay → chi phí bay", _OIL + r"|nhiên liệu bay|jet ?a-?1"),
            ("khách quốc tế, du lịch → doanh thu", r"khách quốc tế|lượng khách|du lịch"), ("tỷ giá USD → nợ vay, thuê tàu bay", _FX)),
    "GVR": (("giá cao su → doanh thu mủ", r"giá cao su|cao su thiên nhiên"),
            ("FDI, chuyển đổi đất khu công nghiệp → cho thuê đất", r"\bfdi\b|khu công nghiệp|vốn đầu tư nước ngoài")),
    "KBC": (("FDI, thuế Mỹ → nhu cầu thuê đất khu công nghiệp", r"\bfdi\b|vốn đầu tư nước ngoài|khu công nghiệp|" + _TRADE),),
    "DGC": (("giá phốt pho vàng, hóa chất → giá bán", r"phốt pho|photpho|phosphor|giá hóa chất"),
            ("chip, bán dẫn → nhu cầu phốt pho", r"bán dẫn|chip")),
    "PNJ": (("giá vàng → giá bán, nhu cầu trang sức", r"giá vàng"), ("sức mua → doanh thu bán lẻ", _SPENDING)),
    "REE": _POWER_GEN, "POW": _POWER_GEN, "PC1": _POWER_GEN + (("đầu tư lưới điện, quy hoạch điện → xây lắp điện", r"lưới điện|đường dây 500"),),
    "GMD": _PORTS, "HAH": _PORTS,
    "VHC": _PANGASIUS, "ANV": _PANGASIUS,
    "VHM": _DEVELOPER, "VIC": _DEVELOPER + (("VinFast, thị trường ô tô điện → lỗ / tăng trưởng mảng xe", r"vinfast|ô tô điện|xe điện"),),
    "NLG": _DEVELOPER, "KDH": _DEVELOPER, "DXG": _DEVELOPER, "PDR": _DEVELOPER,
    "VRE": (("sức mua → doanh thu cho thuê trung tâm thương mại", _SPENDING),),
}.items()}


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
    """Label of the first SECTOR_TERMS / DRIVERS row that applies to the ticker and matches the title. For DRIVERS, a
    ticker with its own TICKER_CHANNELS is matched on those instead."""
    if LISTICLE_RE.search(title) or _FILING_RE.match(title):
        return None
    if table is DRIVERS and ticker in TICKER_CHANNELS:
        return next((label for label, pat in TICKER_CHANNELS[ticker] if pat.search(title)), None)
    keys = _keys(ticker, sector, tags)
    return next((label for label, applies, pat in table if applies & keys and pat.search(title)), None)


def channels_for(ticker: str, sector: str | None, tags: list[str]) -> list[str]:
    """Impact-channel labels the ticker's related news is matched on (its own, else its sector's)."""
    if ticker in TICKER_CHANNELS:
        return [label for label, _ in TICKER_CHANNELS[ticker]]
    keys = _keys(ticker, sector, tags)
    return [label for label, applies, _ in DRIVERS if applies & keys]


def select_news(articles: list[dict], meta: dict[str, dict], per_ticker: int,
                sector_per_ticker: int = SECTOR_NEWS_PER_TICKER,
                related_per_ticker: int = RELATED_NEWS_PER_TICKER,
                per_channel: int = RELATED_NEWS_PER_CHANNEL) -> dict[str, dict[str, list[dict]]]:
    """articles newest first, each {id, title, tickers, ...}; meta = {ticker: {sector, tags, aliases}}.
    Returns {ticker: {"ticker": [...], "sector": [... with `group` label], "related": [... with `group` channel]}};
    every ticker has all three keys. `per_channel` related articles per channel so one driver does not fill the group."""
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
                    g["ticker"].append({**a, "kind": "ticker"})
                    seen[t].add(key)
                continue
            if len(g["sector"]) < sector_per_ticker and (label := match(SECTOR_TERMS, t, sector, tags, a["title"])):
                g["sector"].append({**a, "kind": "sector", "group": label})
                seen[t].add(key)
                continue
            if len(g["related"]) < related_per_ticker and (label := match(DRIVERS, t, sector, tags, a["title"])) \
                    and sum(d["group"] == label for d in g["related"]) < per_channel:
                g["related"].append({**a, "kind": "related", "group": label})
                seen[t].add(key)
    return out
