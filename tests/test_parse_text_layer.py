"""core/parse.py — detect and bypass an unusable hidden text layer in scanned PDFs."""
import pytest

pymupdf = pytest.importorskip("pymupdf")

from core.parse import _rasterize_pdf, text_layer_problem

ACCENTED = "Ngân hàng Thương mại Cổ phần Quân đội báo cáo tài chính riêng cho năm kết thúc ngày 31 tháng 12 năm 2024. " * 30
PLAIN = "Ngan hang Thuong mai Co phan Quan doi bao cao tai chinh rieng cho nam ket thuc ngay 31 thang 12 nam 2024. " * 30


def _pdf(tmp_path, text, name="a.pdf"):
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(page.rect + (20, 20, -20, -20), text, fontsize=6, fontname="helv")
    p = tmp_path / name
    doc.save(p)
    return str(p)


def test_plain_ascii_text_layer_is_flagged(tmp_path):
    assert text_layer_problem(_pdf(tmp_path, PLAIN)) == "no diacritics"


def test_diacritic_text_layer_is_usable(tmp_path):
    # Helvetica cannot draw most Vietnamese letters, so build the text with the diacritic ratio of real Vietnamese
    # but check the decision function on the extracted text instead of on glyph rendering
    from core import parse
    ratio = len(parse._DIACRITIC_RE.findall(ACCENTED)) / sum(c.isalpha() for c in ACCENTED)
    assert ratio > 0.15 > parse._MIN_DIACRITIC_RATIO


def test_pdf_without_text_layer_is_left_alone(tmp_path):
    assert text_layer_problem(_pdf(tmp_path, "")) is None


def test_one_short_cover_page_among_text_free_pages_is_left_alone(tmp_path):
    # a plain scan whose cover page has a few words: 1 stray page of 5 stays under the share limit
    doc = pymupdf.open()
    for i in range(5):
        page = doc.new_page()
        if i == 0:
            page.insert_text((50, 50), "Ngan hang Quan doi", fontsize=10)
    p = tmp_path / "scan.pdf"
    doc.save(p)
    assert text_layer_problem(str(p)) is None


def test_scanned_pages_with_a_stray_footer_are_flagged(tmp_path):
    # GAS: 116 of 118 pages were scans carrying only a ~24-character signature footer
    doc = pymupdf.open()
    for _ in range(6):
        page = doc.new_page()
        page.insert_text((50, 800), "Van ban nay duoc xac thuc", fontsize=8)
    p = tmp_path / "stray.pdf"
    doc.save(p)
    assert text_layer_problem(str(p)) == "stray text only"


def test_rasterised_copy_keeps_pages_and_loses_the_text_layer(tmp_path):
    src = _pdf(tmp_path, PLAIN)
    out = _rasterize_pdf(src)
    try:
        doc = pymupdf.open(out)
        assert len(doc) == 1 and doc[0].get_text().strip() == "" and len(doc[0].get_images()) == 1
        assert doc[0].rect.width == pymupdf.open(src)[0].rect.width
    finally:
        import os
        os.unlink(out)


# ── two-engine parse: Tesseract everywhere, RapidOCR on the statement pages when the balance check fails ──────────

from core import parse as parse_mod  # noqa: E402

BS_PAGE = "BẢNG CÂN ĐỐI KẾ TOÁN RIÊNG\nngày 31 tháng 12 năm 2025\n|100|A. TÀI SẢN NGẮN HẠN||370.623.744|250.759.416|"
CAPITAL_ONLY = "|440|TỔNG CỘNG NGUỒN VỐN||590.372.684|436.738.860|"
BOTH = "|270|TỔNG CỘNG TÀI SẢN||590.372.684|436.738.860|\n" + CAPITAL_ONLY
NOTES_PAGE = "THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG\nMẪU SỐ B 09-DN\n|1|Tiền|10|20|"


def test_statement_pages_picks_primary_statements_and_not_the_notes():
    pages = ["BÁO CÁO CỦA BAN GIÁM ĐỐC", BS_PAGE, "MẪU SỐ B 02-DN\n|doanh thu|1|2|", NOTES_PAGE,
             "BÁO CÁO TÌNH HÌNH TÀI CHÍNH RIÊNG", "B03/TCTD (Ban hành theo Thông tư"]
    assert parse_mod.statement_pages(pages) == [1, 2, 4, 5]


def test_statement_markers_are_only_read_from_the_page_head():
    late = "x" * 600 + " BẢNG CÂN ĐỐI KẾ TOÁN"           # a notes page that merely mentions the statement further down
    assert parse_mod.statement_pages([late]) == []


def _stub_ocr(monkeypatch, tess_pages, rapid_pages):
    calls = []

    def fake_ocr_pages(path, lang, fn, only=None):
        calls.append((getattr(fn, "__module__", ""), only))
        return list(tess_pages) if only is None else [rapid_pages[i] for i in only]

    monkeypatch.setattr(parse_mod, "_ocr_pages", fake_ocr_pages)
    monkeypatch.setattr(parse_mod, "text_layer_problem", lambda p: None)
    monkeypatch.setattr(parse_mod, "_count_pages_pymupdf", lambda p: len(tess_pages))
    return calls


def test_failed_balance_check_swaps_in_the_rapidocr_statement_pages(monkeypatch):
    pytest.importorskip("rapidocr")
    tess = ["cover", BS_PAGE + "\n" + CAPITAL_ONLY, NOTES_PAGE]
    rapid = {1: BS_PAGE + "\n" + BOTH}
    calls = _stub_ocr(monkeypatch, tess, rapid)
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"] == "tesseract + rapidocr (statement pages)"
    assert BOTH in doc.content and NOTES_PAGE in doc.content and doc.content.startswith("cover")
    assert calls[1][1] == [1]                       # only the statement page was re-read


def test_a_passing_balance_check_never_runs_the_second_engine(monkeypatch):
    tess = [BS_PAGE + "\n" + BOTH, NOTES_PAGE]
    calls = _stub_ocr(monkeypatch, tess, {})
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"] == "tesseract" and len(calls) == 1


def test_the_second_engine_result_is_kept_only_if_it_passes_the_check(monkeypatch):
    pytest.importorskip("rapidocr")
    tess = [BS_PAGE + "\n" + CAPITAL_ONLY]
    calls = _stub_ocr(monkeypatch, tess, {0: BS_PAGE + "\n" + CAPITAL_ONLY})     # RapidOCR does not help either
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"] == "tesseract" and doc.content == tess[0]


# ── digit repair from the second engine ────────────────────────────────────────────────────────────

def test_repair_replaces_a_misread_number_with_the_unique_near_match_keeping_separators():
    text = "|270|TỔNG CỘNG TÀI SẢN||2.735.488.878|2.356.000.100|"
    fixed, n = parse_mod.repair_numbers(text, {"2735388878", "2356000100"})
    assert n == 1 and fixed == "|270|TỔNG CỘNG TÀI SẢN||2.735.388.878|2.356.000.100|"


def test_repair_leaves_numbers_the_second_engine_agrees_with_or_cannot_decide():
    trusted = {"2735388878", "2735388879", "9999999999"}
    text = "2.735.388.878 | 2.735.388.870 | 1.234.567.890 | 12.345 | 2.735.4"
    # exact match kept; two equally near candidates -> ambiguous, kept; no near candidate kept; short numbers kept
    assert parse_mod.repair_numbers(text, trusted) == (text, 0)


def test_repair_ignores_other_lengths_and_far_candidates():
    assert parse_mod.repair_numbers("|1.000.000.000|", {"100000000"}) == ("|1.000.000.000|", 0)         # length differs
    assert parse_mod.repair_numbers("|1.234.567.890|", {"1234500000"}) == ("|1.234.567.890|", 0)          # > 2 digits off


def _stub_two_engines(monkeypatch, tess_pages, oracle=None, rapid_pages=None):
    monkeypatch.setattr(parse_mod, "_ocr_pages", lambda path, lang, fn, only=None: list(tess_pages) if only is None
                        else [rapid_pages[i] for i in only])
    monkeypatch.setattr(parse_mod, "_rapidocr_numbers", lambda path, idx: {i: set(oracle or ()) for i in idx})
    monkeypatch.setattr(parse_mod, "text_layer_problem", lambda p: None)
    monkeypatch.setattr(parse_mod, "_count_pages_pymupdf", lambda p: len(tess_pages))


def test_digit_repair_keeps_tesseract_text_and_fixes_the_total(monkeypatch):
    pytest.importorskip("rapidocr")
    page = "BẢNG CÂN ĐỐI KẾ TOÁN\n|270|TỔNG CỘNG TÀI SẢN||2.735.488.878|1|\n|440|TỔNG CỘNG NGUỒN VỐN||2.735.388.878|1|"
    _stub_two_engines(monkeypatch, [page], oracle={"2735388878"})
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"].startswith("tesseract + rapidocr digits")
    assert "TỔNG CỘNG TÀI SẢN||2.735.388.878" in doc.content and "ẢNG CÂN ĐỐI KẾ TOÁN" in doc.content


def test_digit_repair_that_does_not_balance_falls_back_to_the_page_swap(monkeypatch):
    pytest.importorskip("rapidocr")
    broken = "BẢNG CÂN ĐỐI KẾ TOÁN\n" + CAPITAL_ONLY                         # total-assets row dropped: nothing to repair
    rapid = "BẢNG CÂN ĐỐI KẾ TOÁN\n" + BOTH
    _stub_two_engines(monkeypatch, [broken], oracle={"590372684"}, rapid_pages={0: rapid})
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"] == "tesseract + rapidocr (statement pages)" and BOTH in doc.content


def test_nothing_that_balances_keeps_the_tesseract_text(monkeypatch):
    pytest.importorskip("rapidocr")
    page = "BẢNG CÂN ĐỐI KẾ TOÁN\n|270|TỔNG CỘNG TÀI SẢN||2.735.488.878|1|\n|440|TỔNG CỘNG NGUỒN VỐN||9.999.999.999|1|"
    _stub_two_engines(monkeypatch, [page], oracle={"2735388878"}, rapid_pages={0: page})
    doc = parse_mod.parse_with_pymupdf("x.pdf")
    assert doc.metadata["ocr_engine"] == "tesseract" and doc.content == page


def test_region_totals_block_satisfies_the_balance_check():
    from core.balance_check import balance_check
    from core.parse import _group, _totals_block

    assert _group("87090620643636") == "87.090.620.643.636"
    block = _totals_block((["87090620643636", "78781946257137"], ["87090620643636", "78781946257137"]))
    garbled = "| TỔNG CỘNG TÀI SẢN | 270 | 7190620643636 |\n| TỔNG CỘNG NGUỒN VỐN | 440 | 620.643.636 |\n"
    assert balance_check(garbled).status == "mismatch"
    assert balance_check(garbled + block).status == "ok"
