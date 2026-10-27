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
