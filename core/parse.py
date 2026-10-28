"""
Bài 6 — Parse PDF với 3 công cụ: pymupdf4llm, unstructured, llamaparse.
Usage:
    python data/parse.py evals/docs/HGP/ten_file.pdf --all-tools
    python data/parse.py evals/docs/HGP/ten_file.pdf --tool pymupdf
"""

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ParsedDoc:
    content: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        assert self.content.strip(), "Content rỗng sau parse"


# Vietnamese letters that carry a diacritic (≈35-40% of the letters of real Vietnamese text)
_DIACRITIC_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.IGNORECASE
)
_MIN_LETTERS_TO_JUDGE = 1500   # fewer letters in the sampled text layer: too little evidence, trust it
_MIN_DIACRITIC_RATIO = 0.08
_STRAY_MAX_CHARS = 150         # a statement page has hundreds of characters; a watermark/signature footer a few dozen
_STRAY_SHARE = 0.4


def text_layer_problem(path: str, sample_pages: int = 12) -> str | None:
    """Why the PDF's own text layer cannot be trusted, or None when pymupdf4llm can use it as is.

    pymupdf4llm only OCRs pages that carry no text at all, so two kinds of scanned BCTC slip through:
    - a hidden OCR layer without diacritics ("Ngan hang Thuong mai Co phan", or legacy-font "Ti~nm~t") is kept as the text;
    - scanned pages with a few stray characters (digital-signature footer, QR caption) count as "has text" and are never
      read: a 118-page GAS report came out as 14.6k characters.
    PDFs whose pages have no text at all (plain scans) are fine and return None.
    """
    import pymupdf

    doc = pymupdf.open(path)
    n = len(doc)
    pages = range(n) if n <= sample_pages else sorted({round(i * (n - 1) / (sample_pages - 1)) for i in range(sample_pages)})
    texts = [doc[i].get_text() for i in pages]
    letters = sum(ch.isalpha() for t in texts for ch in t)
    if letters >= _MIN_LETTERS_TO_JUDGE and sum(len(_DIACRITIC_RE.findall(t)) for t in texts) / letters < _MIN_DIACRITIC_RATIO:
        return "no diacritics"
    stray = sum(0 < len(t.strip()) < _STRAY_MAX_CHARS for t in texts)
    if texts and stray / len(texts) >= _STRAY_SHARE:
        return "stray text only"
    return None


def _rasterize_pdf(path: str, dpi: int = 150) -> str:
    """Write an image-only copy of the PDF (same page sizes) to a temp file and return its path.

    Rendering draws what a reader sees, so a bad hidden text layer is gone and the page is OCR'd like any other scan.
    150 dpi matches pymupdf4llm's own OCR resolution.
    """
    import pymupdf

    src, out = pymupdf.open(path), pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    out.save(tmp, deflate=True)
    return tmp


# Folded markers that a page's first lines name a primary statement (balance sheet, income, cash flow) or its form number
_STATEMENT_MARKERS = (
    "bangcandoiketoan", "baocaotinhhinhtaichinh", "baocaoketquahoatdong", "baocaoluuchuyentien",
    "b01dn", "b02dn", "b03dn", "b02tctd", "b03tctd", "b04tctd",
)


def statement_pages(page_texts: list[str]) -> list[int]:
    """Indices of the pages whose head names a primary financial statement (notes pages, form B09/B05, are excluded)."""
    from rag.lexical import fold

    return [i for i, text in enumerate(page_texts) if any(m in fold(text[:500]) for m in _STATEMENT_MARKERS)]


def _ocr_pages(path: str, ocr_language: str, ocr_function, only: list[int] | None = None) -> list[str]:
    """Markdown of each page (all pages, or `only`, in order) OCR'd with the given engine."""
    import pymupdf4llm

    chunks = pymupdf4llm.to_markdown(
        path, pages=only, ocr_language=ocr_language, force_ocr=True, ocr_function=ocr_function, page_chunks=True
    )
    return [c["text"] for c in chunks]


_NUMBER_TOKEN = re.compile(r"\d{1,3}(?:[.,]\d{3})+(?:\d{3})*|\d{7,}")
_MIN_REPAIR_DIGITS = 6        # shorter numbers (notes, percentages) are too easy to confuse with each other
_MAX_REPAIR_DISTANCE = 2      # digits that may differ between a misread number and its replacement


def repair_numbers(text: str, trusted: set[str]) -> tuple[str, int]:
    """Replace numbers Tesseract misread with the second engine's reading; returns (text, replacements).

    `trusted` holds digit strings read by RapidOCR. A number of `text` that is not among them is replaced only when exactly one
    trusted number of the same length differs from it in 1-2 digits (the usual 8/5, 3/8, 1/4 confusions). Table structure and
    diacritics stay Tesseract's; the caller keeps the result only if the balance-sheet check passes afterwards.
    """
    by_length: dict[int, list[str]] = {}
    for t in trusted:
        by_length.setdefault(len(t), []).append(t)
    changes = 0

    def fix(m: re.Match) -> str:
        nonlocal changes
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < _MIN_REPAIR_DIGITS or digits in trusted:
            return m.group(0)
        near = [t for t in by_length.get(len(digits), []) if 0 < sum(a != b for a, b in zip(digits, t)) <= _MAX_REPAIR_DISTANCE]
        if len(near) != 1:
            return m.group(0)
        it = iter(near[0])
        changes += 1
        return "".join(next(it) if ch.isdigit() else ch for ch in m.group(0))

    return _NUMBER_TOKEN.sub(fix, text), changes


def _rapidocr_numbers(path: str, page_indices: list[int]) -> dict[int, set[str]]:
    """Digit strings (6+ digits) RapidOCR reads on each page, looking at the page image only (no table rebuilding)."""
    import tempfile

    import pymupdf
    from rapidocr import RapidOCR

    engine, doc, out = RapidOCR(), pymupdf.open(path), {}
    for i in page_indices:
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            doc[i].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY).save(png)
            res = engine(png)
        finally:
            Path(png).unlink(missing_ok=True)
        text = " ".join(res.txts or [])
        out[i] = {d for tok in re.findall(r"\d[\d.,]*\d", text) if len(d := re.sub(r"\D", "", tok)) >= _MIN_REPAIR_DIGITS}
    return out


def _repair_statements(path: str, pages: list[str]) -> tuple[list[str], int] | None:
    """`pages` with misread numbers on the statement pages corrected from RapidOCR, and how many; None if nothing changed.

    Unlike _reocr_statements this keeps Tesseract's table structure and diacritics: RapidOCR is used only as a second reading
    of the digits (on CTG it read the total right but pymupdf4llm rebuilt only 19 of the 26 table rows from its output).
    """
    import importlib.util

    idx = statement_pages(pages)
    if not idx or importlib.util.find_spec("rapidocr") is None:
        return None
    try:
        oracle = _rapidocr_numbers(path, idx)
    except Exception as exc:
        print(f"  [warn] RapidOCR number pass failed: {exc}")
        return None
    repaired, total = list(pages), 0
    for i in idx:
        repaired[i], n = repair_numbers(pages[i], oracle[i])
        total += n
    return (repaired, total) if total else None


def _reocr_statements(path: str, pages: list[str], ocr_language: str) -> list[str] | None:
    """`pages` with the primary-statement pages re-read by RapidOCR, or None when that is not possible.

    Tesseract is built for ~300 dpi, but many BCTC scans embed 90-100 dpi images, where it misreads digits and drops rows
    (VHM: 88% of the numbers right at best). RapidOCR read all 85 numbers of the same pages correctly, but loses part of the
    Vietnamese diacritics, so it is used only for the statement pages and only when Tesseract's result failed the
    balance-sheet check. Retrieval ignores accents, so the weaker labels cost little there.
    """
    import importlib.util

    idx = statement_pages(pages)
    if not idx or importlib.util.find_spec("rapidocr") is None:
        return None
    try:
        from pymupdf4llm.ocr import rapidocr_api

        fresh = _ocr_pages(path, ocr_language, rapidocr_api.exec_ocr, only=idx)
    except Exception as exc:
        print(f"  [warn] RapidOCR pass failed: {exc}")
        return None
    if len(fresh) != len(idx):
        return None
    merged = list(pages)
    for i, text in zip(idx, fresh):
        merged[i] = text
    return merged


# Bump when parsing changes in a way that can fix documents whose balance check failed: ingest/sync_bctc re-parses those once.
PARSE_VERSION = 2
_REGION_DPI = 200
_REGION_MAX_PAGES = 14          # balance sheets sit in the first pages of a BCTC


def _page_rows(res) -> list[list[tuple[float, str]]]:
    """RapidOCR boxes grouped into text rows by vertical centre; each row is [(left x, text)] ordered left to right."""
    items = sorted(((min(p[1] for p in b) + max(p[1] for p in b)) / 2, max(p[1] for p in b) - min(p[1] for p in b),
                    min(p[0] for p in b), t) for b, t in zip(res.boxes, res.txts))
    rows: list[list[tuple[float, str]]] = []
    last_y = last_h = None
    for y, h, x, t in items:
        if rows and abs(y - last_y) <= max(8, 0.6 * h):
            rows[-1].append((x, t))
        else:
            rows.append([(x, t)])
        last_y, last_h = y, h
    return [sorted(r) for r in rows]


def region_totals(path: str, candidates: list[int] | None = None) -> tuple[list[str], list[str]] | None:
    """Total-assets and total-capital rows read straight from the page images by RapidOCR, or None.

    When Tesseract's table rebuild loses or garbles the two total rows (GAS: 7.190.620.643.636 vs 643.636), RapidOCR still
    reads them from the image: boxes are grouped into rows and the row whose label is a total is taken whole. Returns the
    current- and previous-year digit strings of both rows only when the two current-year figures are equal, i.e. the
    balance-sheet identity holds on figures read independently of the first pass.
    """
    import importlib.util

    from core.balance_check import _ASSET_KEYS, _CAPITAL_KEYS, _label_matches
    from rag.lexical import fold

    if importlib.util.find_spec("rapidocr") is None:
        return None
    import pymupdf
    from rapidocr import RapidOCR

    engine, doc = RapidOCR(), pymupdf.open(path)
    pages = candidates or list(range(min(len(doc), _REGION_MAX_PAGES)))
    assets: list[list[str]] = []
    capital: list[list[str]] = []
    for i in pages:
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            doc[i].get_pixmap(dpi=_REGION_DPI, colorspace=pymupdf.csGRAY).save(png)
            res = engine(png)
        finally:
            Path(png).unlink(missing_ok=True)
        if not res.txts:
            continue
        for row in _page_rows(res):
            label = fold("".join(t for _, t in row if not re.search(r"\d{4,}", t)))
            nums = [d for _, t in row for m in re.findall(r"\d[\d.,]*\d", t) if len(d := re.sub(r"\D", "", m)) >= _MIN_REPAIR_DIGITS]
            if len(nums) < 1:
                continue
            if _label_matches(label, _ASSET_KEYS):
                assets.append(nums)
            elif _label_matches(label, _CAPITAL_KEYS):
                capital.append(nums)
    for a in assets:
        for c in capital:
            if a[0] == c[0]:
                return a[:2], c[:2]
    return None


def _group(digits: str) -> str:
    return f"{int(digits):,}".replace(",", ".")


def _totals_block(totals: tuple[list[str], list[str]]) -> str:
    """Markdown rows for the two totals, in the layout balance_check reads."""
    (a, c) = totals
    row = lambda label, nums: f"| {label} | " + " | ".join(_group(n) for n in nums) + " |"
    return ("\n\n| Bảng cân đối kế toán: tổng cộng đọc lại từ ảnh trang | Cuối năm | Đầu năm |\n|---|---|---|\n"
            + row("TỔNG CỘNG TÀI SẢN", a) + "\n" + row("TỔNG CỘNG NGUỒN VỐN", c))


def parse_with_pymupdf(path: str, ocr_language: str = "vie+eng") -> ParsedDoc:
    from pymupdf4llm.ocr import tesseract_api

    from core.balance_check import balance_check

    problem = text_layer_problem(path)
    rasterised = _rasterize_pdf(path) if problem else None
    engine = "tesseract"
    try:
        target = rasterised or path
        # Tesseract is named explicitly: pymupdf4llm would otherwise pick RapidOCR for every page once it is installed
        pages = _ocr_pages(target, ocr_language, tesseract_api.exec_ocr)
        if balance_check("\n\n".join(pages)).status != "ok":
            # 1) keep Tesseract's text, correct its digits from RapidOCR; 2) else swap in RapidOCR's statement pages (recovers
            # dropped rows). Either is kept only if the balance-sheet check passes afterwards.
            repaired = _repair_statements(target, pages)
            if repaired is not None and balance_check("\n\n".join(repaired[0])).status == "ok":
                pages, engine = repaired[0], f"tesseract + rapidocr digits ({repaired[1]} numbers corrected)"
            else:
                fixed = _reocr_statements(target, pages, ocr_language)
                if fixed is not None and balance_check("\n\n".join(fixed)).status == "ok":
                    pages, engine = fixed, "tesseract + rapidocr (statement pages)"
            if balance_check("\n\n".join(pages)).status != "ok":
                # 3) the total rows themselves, read from the page images and appended as rows (kept only if assets == capital)
                # the original file first: re-rendering the 150 dpi raster lost GAS's total rows that the embedded image gives
                scan_pages = sorted(set(statement_pages(pages)) | set(range(_REGION_MAX_PAGES)))
                totals = None
                for source in dict.fromkeys([path, target]):
                    try:
                        totals = region_totals(source, scan_pages)
                    except Exception as exc:
                        print(f"  [warn] RapidOCR region pass failed: {exc}")
                    if totals is not None:
                        break
                if totals is not None:
                    pages = pages[:-1] + [pages[-1] + _totals_block(totals)]
                    engine += " + rapidocr total rows"
    finally:
        if rasterised:
            Path(rasterised).unlink(missing_ok=True)
    md = "\n\n".join(pages)
    num_pages = _count_pages_pymupdf(path)
    return ParsedDoc(
        content=md,
        metadata={
            "tool": "pymupdf4llm",
            "ocr_engine": engine,
            "source_file": Path(path).name,
            "num_pages": num_pages,
            "parsed_at": datetime.now().isoformat(),
            "content_length": len(md),
            "text_layer": f"rasterised ({problem})" if rasterised else "as is",
        },
    )


def _count_pages_pymupdf(path: str) -> int:
    try:
        import pymupdf
        doc = pymupdf.open(path)
        return doc.page_count
    except Exception:
        return -1


def parse_with_unstructured(path: str) -> ParsedDoc:
    """
    unstructured strategy="fast" dùng pdfminer làm engine.
    Với PDF scan (không có text layer), fast strategy trả về rỗng.
    Ở đây dùng pdfminer trực tiếp để simulate hành vi đó và thấy rõ giới hạn.

    Ghi nhận: trên Windows, unstructured strategy="ocr_only" cần poppler
    (không có sẵn trong PATH) nên không chạy được natively.
    """
    from pdfminer.high_level import extract_text
    from pdfminer.layout import LAParams

    # LAParams: phân tích layout — detect cột, bảng theo không gian
    laparams = LAParams(line_margin=0.5, char_margin=2.0, boxes_flow=0.5)
    text = extract_text(path, laparams=laparams)

    if not text.strip():
        text = (
            "# PARSE RESULT: RỖng\n\n"
            "pdfminer không extract được text — file này là PDF scan (không có text layer).\n"
            "unstructured strategy='fast' sẽ trả về kết quả tương tự.\n"
            "Cần strategy='ocr_only' (yêu cầu poppler) hoặc dùng pymupdf4llm với Tesseract."
        )

    return ParsedDoc(
        content=text,
        metadata={
            "tool": "pdfminer (unstructured fast-mode engine)",
            "source_file": Path(path).name,
            "parsed_at": datetime.now().isoformat(),
            "content_length": len(text),
            "note": "PDF scan: pdfminer/unstructured-fast trả về rỗng nếu không có text layer",
        },
    )


def parse_with_llamaparse(path: str) -> ParsedDoc:
    """
    LlamaParse cần API key (LLAMA_CLOUD_API_KEY).
    Nếu không có key, raise rõ ràng.
    """
    import os
    api_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "LLAMA_CLOUD_API_KEY chưa set. "
            "Đăng ký tại https://cloud.llamaindex.ai và thêm vào .env"
        )
    try:
        from llama_parse import LlamaParse
    except ImportError:
        raise ImportError("Chưa cài llama-parse. Chạy: pip install llama-parse")

    parser = LlamaParse(api_key=api_key, result_type="markdown")
    documents = parser.load_data(path)
    content = "\n\n".join(doc.text for doc in documents)
    return ParsedDoc(
        content=content,
        metadata={
            "tool": "llamaparse",
            "source_file": Path(path).name,
            "num_docs": len(documents),
            "parsed_at": datetime.now().isoformat(),
            "content_length": len(content),
        },
    )


def run_all_tools(pdf_path: str, output_dir: str = "outputs") -> None:
    out = Path(output_dir)
    out.mkdir(exist_ok=True)
    stem = Path(pdf_path).stem[:40]

    tools = [
        ("pymupdf", parse_with_pymupdf),
        ("unstructured", parse_with_unstructured),
        ("llamaparse", parse_with_llamaparse),
    ]

    for name, fn in tools:
        out_file = out / f"hpg_{name}.md"
        print(f"\n--- {name} ---")
        try:
            doc = fn(pdf_path)
            out_file.write_text(doc.content, encoding="utf-8")
            print(f"  OK  {len(doc.content):,} chars → {out_file}")
            for k, v in doc.metadata.items():
                if k != "tool":
                    print(f"       {k}: {v}")
        except Exception as e:
            print(f"  FAIL  {e}")
            out_file.write_text(f"# PARSE FAILED\n\nError: {e}\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Parse PDF bằng nhiều công cụ")
    parser.add_argument("pdf_path", help="Đường dẫn đến file PDF")
    parser.add_argument("--all-tools", action="store_true", help="Chạy cả 3 công cụ")
    parser.add_argument(
        "--tool",
        choices=["pymupdf", "unstructured", "llamaparse"],
        help="Chỉ chạy 1 công cụ",
    )
    parser.add_argument("--output-dir", default="outputs", help="Thư mục xuất markdown")
    args = parser.parse_args()

    if not Path(args.pdf_path).exists():
        print(f"Không tìm thấy file: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    if args.all_tools:
        run_all_tools(args.pdf_path, args.output_dir)
    elif args.tool:
        fn_map = {
            "pymupdf": parse_with_pymupdf,
            "unstructured": parse_with_unstructured,
            "llamaparse": parse_with_llamaparse,
        }
        out = Path(args.output_dir)
        out.mkdir(exist_ok=True)
        out_file = out / f"hpg_{args.tool}.md"
        try:
            doc = fn_map[args.tool](args.pdf_path)
            out_file.write_text(doc.content, encoding="utf-8")
            print(f"OK: {out_file} ({len(doc.content):,} chars)")
        except Exception as e:
            print(f"FAIL: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
