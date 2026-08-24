"""
ingest/extract_facts.py — Extract financial facts from markdown into Postgres.

Numbers never pass through the model — only the SQL query does.
The LLM does one thing: parse markdown tables → typed schema.
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
    period: str
    report_type: Literal["standalone", "consolidated"]
    metric_code: str
    value: float
    unit: str
    source_file: str
    source_page: int
    source: Literal["pdf", "vnstock"] = "pdf"


class ValidationError(BaseModel):
    type: str
    message: str


# ── Validation ────────────────────────────────────────────────────────────────

def validate_facts(facts: list[FinancialFact]) -> list[ValidationError]:
    errors: list[ValidationError] = []

    # 1. Do not mix standalone and consolidated in the same fact set
    report_type_set = {f.report_type for f in facts}
    if len(report_type_set) > 1:
        errors.append(ValidationError(
            type="mixed_report_type",
            message=f"Mixed report types: {report_type_set}",
        ))

    # 2. Total assets = liabilities + equity (deviation < 1%)
    by_period: dict[str, list[FinancialFact]] = {}
    for f in facts:
        by_period.setdefault(f.period, []).append(f)

    for period, period_facts in by_period.items():
        def get(code: str) -> float | None:
            return next((f.value for f in period_facts if f.metric_code == code), None)

        tong_ts = get("tong_tai_san")
        no_pt   = get("no_phai_tra")
        vcsh    = get("von_chu_so_huu")

        if tong_ts and no_pt and vcsh and tong_ts != 0:
            diff_ratio = abs(tong_ts - (no_pt + vcsh)) / tong_ts
            if diff_ratio > 0.01:
                errors.append(ValidationError(
                    type="balance_sheet_mismatch",
                    message=(
                        f"Period {period}: tong_tai_san={tong_ts:,.0f} != "
                        f"no_phai_tra+von_chu_so_huu={no_pt+vcsh:,.0f} "
                        f"(diff {diff_ratio:.1%})"
                    ),
                ))

    # 3. Metric consistent across periods — no >500% swing
    by_metric: dict[str, list[FinancialFact]] = {}
    for f in facts:
        by_metric.setdefault(f.metric_code, []).append(f)

    for metric, metric_facts in by_metric.items():
        nonzero = [f.value for f in metric_facts if f.value > 0]
        if len(nonzero) < 2:
            continue
        ratio = max(nonzero) / min(nonzero)
        if ratio > 6:  # > 500%
            errors.append(ValidationError(
                type="inconsistent_value",
                message=f"{metric}: {ratio:.0f}x swing across periods",
            ))

    return errors


# ── Extraction ────────────────────────────────────────────────────────────────

_TOOL_SCHEMA = {
    "name": "save_financial_facts",
    "description": "Save financial facts extracted from a financial report",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "description": "List of financial metrics",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric_code": {
                            "type": "string",
                            "description": "Snake_case no-diacritic: tong_tai_san, doanh_thu_thuan, ...",
                        },
                        "value": {
                            "type": "number",
                            "description": "Integer VND value, not abbreviated",
                        },
                        "unit": {
                            "type": "string",
                            "description": "Currency unit, usually VND",
                        },
                        "source_page": {
                            "type": "integer",
                            "description": "Page number in original PDF",
                        },
                    },
                    "required": ["metric_code", "value", "unit", "source_page"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
}

_SYSTEM = """Bạn trích xuất số liệu tài chính từ báo cáo tài chính Việt Nam.

Quy tắc đặt tên metric_code (snake_case không dấu):
- tong_tai_san, tai_san_ngan_han, tai_san_dai_han
- no_phai_tra, von_chu_so_huu
- doanh_thu_thuan, loi_nhuan_gop, loi_nhuan_sau_thue
- tien_va_tuong_duong_tien, hang_ton_kho

Quy tắc value:
- Lấy đúng số trong bảng, KHÔNG làm tròn hay rút gọn
- Số trong dấu ngoặc đơn là số âm
- Dấu chấm là phân cách nghìn trong định dạng Việt Nam (1.234.567 = 1234567)

source_page: số trang cuối section (số ở cuối mỗi trang trong markdown)

Chỉ lấy chỉ tiêu cấp 1 (mã số tròn như 100, 200, 270, 300, 400, 440, 10, 20, 60)."""


_FINANCIAL_HEADERS = [
    "bảng cân đối", "kết quả hoạt động kinh doanh", "lưu chuyển tiền",
    "balance sheet", "income statement",
]


def _extract_financial_section(markdown: str, max_chars: int = 20_000) -> str:
    """Cut from first financial header, take up to max_chars."""
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
    period: str,
    report_type: str,
    source_file: str,
    max_chars: int = 20_000,
) -> list[FinancialFact]:
    """Use LLM factory (LLM_PROVIDER) structured output to extract financial facts."""
    client = create_client()
    section = _extract_financial_section(markdown, max_chars)

    response = client.generate(
        messages=[Message(
            role="user",
            content=(
                f"Kỳ báo cáo: {period}\n"
                f"Loại báo cáo: {report_type}\n\n"
                f"Markdown:\n\n{section}"
            ),
        )],
        system=_SYSTEM,
        tools=[_TOOL_SCHEMA],
        max_tokens=4096,
    )

    if not response.tool_calls:
        raise ValueError("Model did not call tool — check LLM_PROVIDER and tool schema")

    raw = response.tool_calls[0].input["facts"]

    return [
        FinancialFact(
            ticker=ticker,
            period=period,
            report_type=report_type,  # type: ignore[arg-type]
            metric_code=f["metric_code"],
            value=f["value"],
            unit=f["unit"],
            source_file=source_file,
            source_page=f["source_page"],
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
                    (ticker, period, report_type, metric_code, value, unit,
                     source_file, source_page, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT financial_facts_unique
                DO UPDATE SET
                    value       = EXCLUDED.value,
                    unit        = EXCLUDED.unit,
                    source_file = EXCLUDED.source_file,
                    source_page = EXCLUDED.source_page
                """,
                [
                    (f.ticker, f.period, f.report_type, f.metric_code,
                     f.value, f.unit, f.source_file, f.source_page, f.source)
                    for f in facts
                ],
            )
    return len(facts)


def query_fact(ticker: str, metric_code: str, period: str) -> list[dict]:
    """Return facts, vnstock source takes priority over pdf."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, period, report_type, metric_code, value, unit,
                       source_page, source
                FROM financial_facts
                WHERE ticker = %s AND metric_code = %s AND period = %s
                ORDER BY CASE WHEN source = 'vnstock' THEN 0 ELSE 1 END,
                         report_type
                """,
                (ticker, metric_code, period),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract financial facts from markdown")
    parser.add_argument("--file",        required=True, help="Markdown file (outputs/2024/hpg_pymupdf.md)")
    parser.add_argument("--ticker",      default="HPG")
    parser.add_argument("--period",      default="2024")
    parser.add_argument("--report-type", dest="report_type", default="standalone",
                        choices=["standalone", "consolidated"])
    parser.add_argument("--dry-run",     action="store_true", help="Print only, no insert")
    parser.add_argument("--query",       nargs=2, metavar=("METRIC_CODE", "PERIOD"),
                        help="Query one metric: --query tong_tai_san 2024")
    args = parser.parse_args()

    if args.query:
        rows = query_fact(args.ticker, args.query[0], args.query[1])
        if not rows:
            print(f"No data: {args.ticker} / {args.query[0]} / {args.query[1]}")
        for row in rows:
            print(row)
        return

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"File not found: {md_path}")
        sys.exit(1)

    markdown = md_path.read_text(encoding="utf-8")
    print(f"Extracting from {md_path.name} ({len(markdown):,} chars)...")

    facts = extract_facts_from_markdown(
        markdown=markdown,
        ticker=args.ticker,
        period=args.period,
        report_type=args.report_type,
        source_file=str(md_path),
    )
    print(f"Extracted {len(facts)} metrics")

    errors = validate_facts(facts)
    for e in errors:
        print(f"  [WARN] {e.type}: {e.message}")

    for f in facts:
        print(f"  {f.metric_code:35s} {f.value:>25,.0f} {f.unit}  (page {f.source_page})")

    if args.dry_run:
        print("--dry-run: no insert")
        return

    n = insert_facts(facts)
    print(f"Inserted {n} rows into financial_facts")


if __name__ == "__main__":
    main()
