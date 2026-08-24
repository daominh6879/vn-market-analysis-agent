"""
data/extract_facts.py — Bài 12: Trích xuất số liệu tài chính vào Postgres.

Số không đi qua model — chỉ câu SQL đi qua.
Claude chỉ làm một việc: parse bảng markdown → đúng schema.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from data.db import get_conn
from llm import create_client
from llm.types import Message


# ── Schema ────────────────────────────────────────────────────────────────────

class FinancialFact(BaseModel):
    ticker: str
    ky: str
    loai_bao_cao: Literal["rieng_le", "hop_nhat"]
    ma_chi_tieu: str
    gia_tri: float
    don_vi: str
    nguon_file: str
    nguon_trang: int


class ValidationError(BaseModel):
    type: str
    message: str


# ── Validation ────────────────────────────────────────────────────────────────

def validate_facts(facts: list[FinancialFact]) -> list[ValidationError]:
    """Ba kiểm tra nghiệp vụ sau khi trích xuất."""
    errors: list[ValidationError] = []

    # 1. Không trộn riêng lẻ và hợp nhất trong cùng một tập facts
    loai_set = {f.loai_bao_cao for f in facts}
    if len(loai_set) > 1:
        errors.append(ValidationError(
            type="mixed_report_type",
            message=f"Trộn lẫn loại báo cáo: {loai_set}",
        ))

    # 2. Tổng tài sản = Nợ phải trả + Vốn chủ sở hữu (chênh lệch < 1%)
    by_ky: dict[str, list[FinancialFact]] = {}
    for f in facts:
        by_ky.setdefault(f.ky, []).append(f)

    for ky, ky_facts in by_ky.items():
        def get(ma: str) -> float | None:
            return next((f.gia_tri for f in ky_facts if f.ma_chi_tieu == ma), None)

        tong_ts = get("tong_tai_san")
        no_pt   = get("no_phai_tra")
        vcsh    = get("von_chu_so_huu")

        if tong_ts and no_pt and vcsh and tong_ts != 0:
            diff_ratio = abs(tong_ts - (no_pt + vcsh)) / tong_ts
            if diff_ratio > 0.01:
                errors.append(ValidationError(
                    type="balance_sheet_mismatch",
                    message=(
                        f"Kỳ {ky}: tong_tai_san={tong_ts:,.0f} ≠ "
                        f"no_phai_tra+vcsh={no_pt+vcsh:,.0f} "
                        f"(chênh {diff_ratio:.1%})"
                    ),
                ))

    # 3. Chỉ tiêu nhất quán giữa các kỳ — không thay đổi > 500%
    by_ma: dict[str, list[FinancialFact]] = {}
    for f in facts:
        by_ma.setdefault(f.ma_chi_tieu, []).append(f)

    for ma, ct_facts in by_ma.items():
        nonzero = [f.gia_tri for f in ct_facts if f.gia_tri > 0]
        if len(nonzero) < 2:
            continue
        ratio = max(nonzero) / min(nonzero)
        if ratio > 6:  # > 500%
            errors.append(ValidationError(
                type="inconsistent_value",
                message=f"{ma}: biến động {ratio:.0f}x giữa các kỳ",
            ))

    return errors


# ── Extraction ────────────────────────────────────────────────────────────────

_TOOL_SCHEMA = {
    "name": "save_financial_facts",
    "description": "Lưu số liệu tài chính trích xuất từ báo cáo",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "description": "Danh sách chỉ tiêu tài chính",
                "items": {
                    "type": "object",
                    "properties": {
                        "ma_chi_tieu": {
                            "type": "string",
                            "description": "Snake_case không dấu: tong_tai_san, doanh_thu_thuan, ...",
                        },
                        "gia_tri": {
                            "type": "number",
                            "description": "Giá trị số nguyên VND, không rút gọn",
                        },
                        "don_vi": {
                            "type": "string",
                            "description": "Đơn vị tiền tệ, thường là VND",
                        },
                        "nguon_trang": {
                            "type": "integer",
                            "description": "Số trang trong PDF gốc",
                        },
                    },
                    "required": ["ma_chi_tieu", "gia_tri", "don_vi", "nguon_trang"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
}

_SYSTEM = """Bạn trích xuất số liệu tài chính từ báo cáo tài chính Việt Nam.

Quy tắc đặt tên ma_chi_tieu (snake_case không dấu):
- tong_tai_san, tai_san_ngan_han, tai_san_dai_han
- no_phai_tra, von_chu_so_huu
- doanh_thu_thuan, loi_nhuan_gop, loi_nhuan_sau_thue
- tien_va_tuong_duong_tien, hang_ton_kho

Quy tắc gia_tri:
- Lấy đúng số trong bảng, KHÔNG làm tròn hay rút gọn
- Số trong dấu ngoặc đơn là số âm
- Dấu chấm là phân cách nghìn trong định dạng Việt Nam (1.234.567 = 1234567)

nguon_trang: số trang cuối section (số ở cuối mỗi trang trong markdown)

Chỉ lấy chỉ tiêu cấp 1 (mã số tròn như 100, 200, 270, 300, 400, 440, 10, 20, 60)."""


_FINANCIAL_HEADERS = [
    "bảng cân đối", "kết quả hoạt động kinh doanh", "lưu chuyển tiền",
    "balance sheet", "income statement",
]


def _extract_financial_section(markdown: str, max_chars: int = 20_000) -> str:
    """Cắt từ header tài chính đầu tiên, lấy tối đa max_chars."""
    lower = markdown.lower()
    start = len(markdown)
    for header in _FINANCIAL_HEADERS:
        idx = lower.find(header)
        if 0 < idx < start:
            start = idx
    if start == len(markdown):
        start = 0
    return markdown[start: start + max_chars]


def extract_facts_from_markdown(
    markdown: str,
    ticker: str,
    ky: str,
    loai_bao_cao: str,
    nguon_file: str,
    max_chars: int = 20_000,
) -> list[FinancialFact]:
    """Dùng LLM factory (LLM_PROVIDER) structured output trích xuất số liệu tài chính."""
    client = create_client()
    section = _extract_financial_section(markdown, max_chars)

    response = client.generate(
        messages=[Message(
            role="user",
            content=(
                f"Kỳ báo cáo: {ky}\n"
                f"Loại báo cáo: {loai_bao_cao}\n\n"
                f"Markdown:\n\n{section}"
            ),
        )],
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        max_tokens=4096,
    )

    if not response.tool_calls:
        raise ValueError("Model không gọi tool — kiểm tra LLM_PROVIDER và tool schema")

    raw = response.tool_calls[0].input["facts"]

    return [
        FinancialFact(
            ticker=ticker,
            ky=ky,
            loai_bao_cao=loai_bao_cao,  # type: ignore[arg-type]
            ma_chi_tieu=f["ma_chi_tieu"],
            gia_tri=f["gia_tri"],
            don_vi=f["don_vi"],
            nguon_file=nguon_file,
            nguon_trang=f["nguon_trang"],
        )
        for f in raw
    ]


# ── DB ─────────────────────────────────────────────────────────────────────────

def insert_facts(facts: list[FinancialFact]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO financial_facts
                    (ticker, ky, loai_bao_cao, ma_chi_tieu, gia_tri, don_vi,
                     nguon_file, nguon_trang)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, ky, loai_bao_cao, ma_chi_tieu)
                DO UPDATE SET
                    gia_tri    = EXCLUDED.gia_tri,
                    don_vi     = EXCLUDED.don_vi,
                    nguon_file = EXCLUDED.nguon_file,
                    nguon_trang = EXCLUDED.nguon_trang
                """,
                [
                    (f.ticker, f.ky, f.loai_bao_cao, f.ma_chi_tieu,
                     f.gia_tri, f.don_vi, f.nguon_file, f.nguon_trang)
                    for f in facts
                ],
            )
    return len(facts)


def query_fact(ticker: str, ma_chi_tieu: str, ky: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, ky, loai_bao_cao, ma_chi_tieu, gia_tri, don_vi, nguon_trang
                FROM financial_facts
                WHERE ticker = %s AND ma_chi_tieu = %s AND ky = %s
                ORDER BY loai_bao_cao
                """,
                (ticker, ma_chi_tieu, ky),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Trích xuất số liệu tài chính")
    parser.add_argument("--file",   required=True, help="Markdown file (outputs/2024/hpg_pymupdf.md)")
    parser.add_argument("--ticker", default="HPG")
    parser.add_argument("--ky",     default="2024")
    parser.add_argument("--loai",   default="rieng_le", choices=["rieng_le", "hop_nhat"])
    parser.add_argument("--dry-run", action="store_true", help="In ra, không insert")
    parser.add_argument("--query",  nargs=2, metavar=("MA_CHI_TIEU", "KY"),
                        help="SELECT ra một chỉ tiêu: --query tong_tai_san 2024")
    args = parser.parse_args()

    if args.query:
        rows = query_fact(args.ticker, args.query[0], args.query[1])
        if not rows:
            print(f"Không có dữ liệu: {args.ticker} / {args.query[0]} / {args.query[1]}")
        for row in rows:
            print(row)
        return

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"File không tồn tại: {md_path}")
        sys.exit(1)

    markdown = md_path.read_text(encoding="utf-8")
    print(f"Đang trích xuất từ {md_path.name} ({len(markdown):,} chars)...")

    facts = extract_facts_from_markdown(
        markdown=markdown,
        ticker=args.ticker,
        ky=args.ky,
        loai_bao_cao=args.loai,
        nguon_file=str(md_path),
    )
    print(f"Trích được {len(facts)} chỉ tiêu")

    errors = validate_facts(facts)
    for e in errors:
        print(f"  [WARN] {e.type}: {e.message}")

    for f in facts:
        print(f"  {f.ma_chi_tieu:35s} {f.gia_tri:>25,.0f} {f.don_vi}  (trang {f.nguon_trang})")

    if args.dry_run:
        print("--dry-run: không insert")
        return

    n = insert_facts(facts)
    print(f"Đã insert {n} rows vào financial_facts")


if __name__ == "__main__":
    main()
