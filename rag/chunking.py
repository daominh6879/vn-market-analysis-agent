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
