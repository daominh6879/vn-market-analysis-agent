"""
Bài 6 — Parse PDF với 3 công cụ: pymupdf4llm, unstructured, llamaparse.
Usage:
    python data/parse.py evals/docs/HGP/ten_file.pdf --all-tools
    python data/parse.py evals/docs/HGP/ten_file.pdf --tool pymupdf
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ParsedDoc:
    content: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        assert self.content.strip(), "Content rỗng sau parse"


def parse_with_pymupdf(path: str, ocr_language: str = "vie+eng") -> ParsedDoc:
    import pymupdf4llm
    md = pymupdf4llm.to_markdown(path, ocr_language=ocr_language, force_ocr=True)
    num_pages = _count_pages_pymupdf(path)
    return ParsedDoc(
        content=md,
        metadata={
            "tool": "pymupdf4llm",
            "source_file": Path(path).name,
            "num_pages": num_pages,
            "parsed_at": datetime.now().isoformat(),
            "content_length": len(md),
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
