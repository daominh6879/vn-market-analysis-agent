"""
rag/chunking.py — Bài 7: 3 chiến lược chunking cho văn bản tài chính.
"""
from __future__ import annotations

import re
from typing import NamedTuple


# ── 1. Cắt cố định có chồng lấn ─────────────────────────────────────────────

def chunk_fixed(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    """Cắt theo số ký tự cố định, chồng lấn overlap ký tự."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    return [c for c in splitter.split_text(text) if c.strip()]


# ── 2. Cắt theo cấu trúc đoạn (structural) ──────────────────────────────────

def chunk_structural(text: str, max_size: int = 800) -> list[str]:
    """
    Tách tại ranh giới tự nhiên: heading markdown và dòng trống đôi.
    Không phá vỡ giữa câu. Nếu đoạn vượt max_size thì cắt thêm bằng splitter.
    """
    from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter

    # Ưu tiên tách theo markdown heading trước
    md_splitter = MarkdownTextSplitter(chunk_size=max_size, chunk_overlap=0)
    coarse = md_splitter.split_text(text)

    # Đoạn nào vẫn > max_size → cắt thêm
    fallback = RecursiveCharacterTextSplitter(
        chunk_size=max_size,
        chunk_overlap=50,
        separators=["\n\n", "\n", ".", " "],
    )
    result: list[str] = []
    for seg in coarse:
        if len(seg) > max_size:
            result.extend(fallback.split_text(seg))
        else:
            if seg.strip():
                result.append(seg.strip())
    return result


# ── 3. Cắt hai tầng (hierarchical) ──────────────────────────────────────────

class HierarchicalChunk(NamedTuple):
    child: str   # chunk nhỏ ~400 ký tự — dùng để embed & tìm kiếm
    parent: str  # đoạn cha ~1200 ký tự — đưa vào context model


def chunk_hierarchical(text: str, child_size: int = 400, parent_size: int = 1200) -> list[HierarchicalChunk]:
    """
    Tầng 1 (parent): tách đoạn lớn ~1200 ký tự.
    Tầng 2 (child): mỗi parent chia thành các child ~400 ký tự.
    Retrieval dùng child vector, nhưng trả context = parent text.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=parent_size, chunk_overlap=100,
        separators=["\n\n", "\n", ".", " "],
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=child_size, chunk_overlap=40,
        separators=["\n\n", "\n", ".", " "],
    )

    result: list[HierarchicalChunk] = []
    for parent in parent_splitter.split_text(text):
        if not parent.strip():
            continue
        children = child_splitter.split_text(parent)
        for child in children:
            if child.strip():
                result.append(HierarchicalChunk(child=child.strip(), parent=parent.strip()))
    return result


# ── Ngữ cảnh bảng cho chunk giữa bảng ────────────────────────────────────────

_DATE_LINE_PREFIXES = ("taingay", "chonam", "donvi", "donvitinh")
_HEADER_HINTS = ("maso", "thuyetminh", "chitieu")
_HEADER_PERIOD_HINTS = ("cuoinam", "daunam", "namnay", "namtruoc", "nam20", "31122")


# Statement names found in the title line (folded keyword → canonical name), checked in this order
_CANON_TITLES = (
    ("thuyetminhbaocaotaichinh", "THUYẾT MINH BÁO CÁO TÀI CHÍNH"),
    ("bangcandoiketoan", "BẢNG CÂN ĐỐI KẾ TOÁN"),
    ("baocaotinhhinhtaichinh", "BÁO CÁO TÌNH HÌNH TÀI CHÍNH"),
    ("baocaoketquahoatdong", "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG"),
    ("baocaoluuchuyentien", "BÁO CÁO LƯU CHUYỂN TIỀN TỆ"),
    ("kiemtoandoclap", "BÁO CÁO KIỂM TOÁN ĐỘC LẬP"),
)
# "Mẫu số B 09-DN" / "Mẫu B02/TCTD" is printed on every page of a statement and survives OCR better than its title
_FORM_RE = re.compile(r"(?:mau(?:so)?)?b(0\d)(dn|tctd)")
# what may precede a statement name in a running page header: nothing, or the company's name
_COMPANY_WORDS = ("congty", "nganhang", "tapdoan", "ctcp", "tctd")
_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
_FORM_TITLES = {
    "dn": {"01": "BẢNG CÂN ĐỐI KẾ TOÁN", "02": "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
           "03": "BÁO CÁO LƯU CHUYỂN TIỀN TỆ", "09": "THUYẾT MINH BÁO CÁO TÀI CHÍNH"},
    "tctd": {"02": "BÁO CÁO TÌNH HÌNH TÀI CHÍNH", "03": "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG",
             "04": "BÁO CÁO LƯU CHUYỂN TIỀN TỆ", "05": "THUYẾT MINH BÁO CÁO TÀI CHÍNH"},
}


def _title_of(line: str) -> str | None:
    """Canonical statement name of a heading / running page-header line, tolerant of OCR damage; else None.

    A real title is the whole heading, optionally preceded by the company name. "Thông tin bổ sung về báo cáo lưu
    chuyển tiền tệ" or "Các khoản mục ngoài bảng cân đối kế toán" are notes sub-headings that merely mention a statement.
    """
    from rag.lexical import fold

    plain = line.strip().strip("#*_ ").strip()
    if not plain or plain.startswith("|") or len(plain) > 200:
        return None
    f = fold(plain)
    form = _FORM_RE.search(f)
    if form and form.start() <= 6 and form.group(1) in _FORM_TITLES[form.group(2)]:
        return _FORM_TITLES[form.group(2)][form.group(1)]
    opening = [c for c in plain if c.isalpha()][:28]
    if len(opening) < 8 or sum(c.isupper() for c in opening) / len(opening) < 0.7:
        return None                                   # body text that merely mentions a statement
    for key, title in _CANON_TITLES:
        at = f.find(key)
        if at >= 0 and (at == 0 or any(w in f[:at] for w in _COMPANY_WORDS)):
            return title
    return None


def _is_unrecognised_heading(line: str) -> bool:
    """An upper-case "BÁO CÁO ..."/"BẢNG ..." heading that is not a known statement (e.g. the board's report).

    It ends the previous statement, so chunks after it must not inherit that statement's label.
    """
    from rag.lexical import fold

    plain = line.strip().strip("#*_ ").strip()
    letters = [c for c in plain if c.isalpha()]
    return (4 <= len(plain) <= 90 and len(letters) >= 8 and sum(c.isupper() for c in letters[:28]) / len(letters[:28]) >= 0.7
            and fold(plain).startswith(("bang", "baocao")))


def _header_cells(line: str) -> list[str] | None:
    """Cells of a table header row (has a "Mã số"/"Thuyết minh"/"Chỉ tiêu" label and a period label), else None."""
    from rag.lexical import fold

    if not line.lstrip().startswith("|"):
        return None
    f = fold(line)
    labelled = any(h in f for h in _HEADER_HINTS) and any(h in f for h in _HEADER_PERIOD_HINTS)
    if not (labelled or len(_DATE_RE.findall(line)) >= 2):       # labels can be garbled; two period dates are not
        return None
    cells = [re.sub(r"\s+", " ", c.replace("<br>", " ")).strip(" *_") for c in line.strip().strip("|").split("|")]
    return [c for c in cells if c and not set(c) <= set("-: ")]


def add_table_context(text: str, chunks: list[str]) -> list[str]:
    """Prefix chunks that start mid-table with "[Ngữ cảnh: <statement> | <date> | Cột: <header cells>]".

    A table continued on the next page has no title or column labels, so a model cannot tell which column is the
    end-of-year figure and hedges ("không nêu rõ đây là..."). The context comes from the nearest title / date / header
    before the chunk in the source text. Chunks that carry their own title or header, and non-table chunks, are kept as is.
    """
    from rag.lexical import fold

    events: list[tuple[int, str, str]] = []     # (position, kind, value) in document order
    pos, last_title_pos = 0, -1
    for line in text.splitlines(keepends=True):
        title = _title_of(line)
        if title:
            events.append((pos, "title", title))
            last_title_pos = pos
        elif _is_unrecognised_heading(line):
            events.append((pos, "reset", ""))
            last_title_pos = -1
        elif 0 <= last_title_pos and pos - last_title_pos < 400 and fold(line).startswith(_DATE_LINE_PREFIXES):
            events.append((pos, "date", line.strip().strip("*_ ")))
        else:
            cells = _header_cells(line)
            if cells:
                events.append((pos, "columns", " | ".join(cells)))
        pos += len(line)

    out: list[str] = []
    search_from = 0
    for chunk in chunks:
        start = text.find(chunk, search_from)
        if start < 0:
            out.append(chunk)
            continue
        search_from = start                       # splitter overlap can start slightly before the previous chunk's end
        end = start + len(chunk)
        inside = {kind for p, kind, _ in events if start <= p < end}
        has_rows = any(ln.lstrip().startswith("|") for ln in chunk.splitlines())
        if not has_rows or inside & {"title", "columns", "reset"}:
            out.append(chunk)
            continue
        state: dict[str, str] = {}
        for p, kind, value in events:
            if p >= start:
                break
            if kind == "reset":
                state = {}                        # unrecognised heading: better no label than the previous statement's
            elif kind == "title":
                if state.get("title") != value:   # the same title repeats on every page of a statement
                    state = {"title": value}      # a new statement drops the previous one's date and columns
            elif kind == "date" and "date" in state:
                state["date"] += f" | {value}"    # "Tại ngày ..." and "Đơn vị: ..." both follow the title
            else:
                state[kind] = value
        parts = [state[k] if k != "columns" else f"Cột: {state[k]}" for k in ("title", "date", "columns") if k in state]
        out.append(f"[Ngữ cảnh: {' | '.join(parts)}]\n{chunk}" if parts else chunk)
    return out


# ── Gắn metadata vào đầu chunk ───────────────────────────────────────────────

def prepend_metadata(chunk: str, meta: dict) -> str:
    """
    Gắn metadata vào đầu chunk trước khi embed.
    Thường cải thiện retrieval hơn cả đổi chiến lược cắt.
    """
    parts = []
    if meta.get("ticker"):
        parts.append(meta["ticker"])
    if meta.get("year"):
        parts.append(str(meta["year"]))
    if meta.get("report_type"):
        parts.append(meta["report_type"])
    header = " | ".join(parts)
    return f"[{header}]\n{chunk}" if header else chunk


# ── CLI quick-check ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "outputs/hpg_pymupdf.md"
    text = open(path, encoding="utf-8").read()

    fixed = chunk_fixed(text)
    structural = chunk_structural(text)
    hierarchical = chunk_hierarchical(text)

    print(f"chunk_fixed      : {len(fixed):4d} chunks")
    print(f"chunk_structural : {len(structural):4d} chunks")
    print(f"chunk_hierarchical: {len(hierarchical):4d} (child,parent) pairs")
    print(f"\nFixed[0]:\n{fixed[0][:300]}")
    print(f"\nStructural[0]:\n{structural[0][:300]}")
    print(f"\nHierarchical[0].child:\n{hierarchical[0].child[:200]}")
    print(f"\nHierarchical[0].parent:\n{hierarchical[0].parent[:400]}")
