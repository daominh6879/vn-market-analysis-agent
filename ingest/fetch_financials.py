"""
ingest/fetch_financials.py — Fetch financial statements from vnstock Finance API → Postgres.

Second data path (no PDF, no LLM):
  vnstock Finance → financial_facts → Postgres (source='vnstock')

Query priority: vnstock > pdf (see extract_facts.query_fact)

Schema:  python ingest/fetch_financials.py --ticker HPG --show-schema
Dry run: python ingest/fetch_financials.py --ticker HPG --dry-run
Run:     python ingest/fetch_financials.py --ticker HPG --period-from 2020 --period-to 2024
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Vietnamese text → snake_case ASCII (strip diacritics).

    "Doanh thu thuần" → "doanh_thu_thuan"
    "Tổng tài sản"    → "tong_tai_san"
    """
    text = unicodedata.normalize("NFD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", "_", text.strip())


_PERIOD_RE = re.compile(r"^\d{4}(-Q[1-4])?$")


def _period_cols(df) -> list[str]:
    return [c for c in df.columns if _PERIOD_RE.match(str(c))]


# ── item_id → metric_code mapping ─────────────────────────────────────────────
# vnstock item_id (English snake_case) → our Vietnamese metric_code convention.
# Covers the 5 keys used by validators + common indicators.
# Unmapped item_ids fall back to slugify(item) (Vietnamese diacritics stripped).

_ITEM_ID_MAP: dict[str, str] = {
    # balance_sheet
    "total_assets":            "tong_tai_san",
    "current_assets":          "tai_san_ngan_han",
    "long_term_assets":        "tai_san_dai_han",
    "total_liabilities":       "no_phai_tra",
    "liabilities":             "no_phai_tra",
    "short_term_liabilities":  "no_ngan_han",
    "long_term_liabilities":   "no_dai_han",
    "owner_equity":            "von_chu_so_huu",
    "equity":                  "von_chu_so_huu",
    "total_equity":            "von_chu_so_huu",
    "shareholders_equity":     "von_chu_so_huu",
    "charter_capital":         "von_dieu_le",
    "cash_and_cash_equivalents": "tien_va_tuong_duong_tien",
    "inventories":             "hang_ton_kho",
    # income_statement
    "net_sales":               "doanh_thu_thuan",
    "revenue":                 "doanh_thu_thuan",
    "net_revenue":             "doanh_thu_thuan",
    "gross_profit":            "loi_nhuan_gop",
    "profit_after_tax":        "loi_nhuan_sau_thue",
    "net_profit":              "loi_nhuan_sau_thue",
    "net_profit_after_tax":    "loi_nhuan_sau_thue",
    "profit_before_tax":       "loi_nhuan_truoc_thue",
    "net_profit_loss_before_tax": "loi_nhuan_truoc_thue",
    "ebit":                    "ebit",
    "ebitda":                  "ebitda",
    "operating_profit":        "loi_nhuan_hoat_dong",
    "cost_of_sales":           "gia_von_hang_ban",
    "interest_expense":        "chi_phi_lai_vay",
    # cash_flow
    "operating_cash_flow":     "dong_tien_hoat_dong",
    "investing_cash_flow":     "dong_tien_dau_tu",
    "financing_cash_flow":     "dong_tien_tai_chinh",
}


# ── KBS parser (rows=indicators, cols=periods) ─────────────────────────────────

def _facts_from_kbs(
    df, ticker: str, report_type: str, vnstock_report: str, period_from: int, period_to: int
) -> list[dict]:
    # KBS: period columns are year strings like '2024', '2023'
    periods = [c for c in _period_cols(df) if period_from <= int(c[:4]) <= period_to]
    if not periods:
        raise ValueError(
            f"No period column in range {period_from}–{period_to}. "
            f"Columns: {df.columns.tolist()}"
        )

    # item_id preferred (reliable English slug); item fallback (slugify VN text)
    has_item_id = "item_id" in df.columns
    item_col = next(
        (c for c in ("item", "Chỉ tiêu", "chi_tieu") if c in df.columns),
        None,
    )
    if item_col is None:
        item_col = next(
            (c for c in df.columns if hasattr(df[c], "dtype") and str(df[c].dtype) == "object"),
            None,
        )
    if item_col is None and not has_item_id:
        raise ValueError(f"Cannot find item name column. Columns: {df.columns.tolist()}")

    facts = []
    for _, row in df.iterrows():
        # Prefer item_id → mapped metric_code; fallback to slugify(item)
        if has_item_id:
            item_id = str(row.get("item_id", "")).strip().lower()
            metric_code = _ITEM_ID_MAP.get(item_id) or (slugify(str(row.get(item_col, ""))) if item_col else item_id)
        else:
            item_name = str(row.get(item_col, "")).strip()
            metric_code = slugify(item_name)

        if not metric_code:
            continue
        for period in periods:
            val = row.get(period)
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if fval == 0 or fval != fval:  # skip 0 and NaN
                continue
            facts.append(_make_fact(ticker, period, report_type, metric_code, fval, vnstock_report))
    return facts


# ── VCI parser (rows=periods, cols=indicators) ─────────────────────────────────

_VCI_NON_INDICATOR = frozenset({
    "ticker", "symbol", "yearreport", "quarterreport", "year", "quarter",
    "yearReport", "quarterReport",
})

_VCI_MAP: dict[str, str] = {
    # income_statement
    "revenue": "doanh_thu_thuan",
    "net_revenue": "doanh_thu_thuan",
    "gross_profit": "loi_nhuan_gop",
    "profit_before_tax": "loi_nhuan_truoc_thue",
    "profit_after_tax": "loi_nhuan_sau_thue",
    "net_profit": "loi_nhuan_sau_thue",
    "net_profit_after_tax": "loi_nhuan_sau_thue",
    "ebit": "ebit",
    "ebitda": "ebitda",
    "operating_profit": "loi_nhuan_hoat_dong",
    "interest_expense": "chi_phi_lai_vay",
    # balance_sheet
    "total_assets": "tong_tai_san",
    "current_assets": "tai_san_ngan_han",
    "long_term_assets": "tai_san_dai_han",
    "total_liabilities": "no_phai_tra",
    "short_term_liabilities": "no_ngan_han",
    "long_term_liabilities": "no_dai_han",
    "equity": "von_chu_so_huu",
    "owner_equity": "von_chu_so_huu",
    "charter_capital": "von_dieu_le",
    # cash_flow
    "operating_cash_flow": "dong_tien_hoat_dong",
    "investing_cash_flow": "dong_tien_dau_tu",
    "financing_cash_flow": "dong_tien_tai_chinh",
}


def _facts_from_vci(
    df, ticker: str, report_type: str, vnstock_report: str, period_from: int, period_to: int
) -> list[dict]:
    period_col = next(
        (c for c in ("yearReport", "year", "quarterReport", "quarter") if c in df.columns),
        None,
    )
    if period_col is None:
        raise ValueError(f"Cannot find period column in VCI DataFrame. Columns: {df.columns.tolist()}")

    indicator_cols = [c for c in df.columns if c not in _VCI_NON_INDICATOR]

    facts = []
    for _, row in df.iterrows():
        try:
            period = str(int(row[period_col]))[:4]
        except (TypeError, ValueError):
            continue
        if not (period_from <= int(period) <= period_to):
            continue

        for col in indicator_cols:
            metric_code = _VCI_MAP.get(col.lower()) or _VCI_MAP.get(col) or slugify(col)
            if not metric_code:
                continue
            val = row.get(col)
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if fval == 0 or fval != fval:
                continue
            facts.append(_make_fact(ticker, period, report_type, metric_code, fval, vnstock_report))
    return facts


def _make_fact(ticker, period, report_type, metric_code, value, vnstock_report) -> dict:
    return dict(
        ticker=ticker,
        period=str(period),
        report_type=report_type,
        metric_code=metric_code,
        value=value,
        unit="VND",
        source_file=f"vnstock_{vnstock_report}",
        source_page=0,
        source="vnstock",
    )


def _parse_df(df, ticker, report_type, vnstock_report, period_from, period_to) -> list[dict]:
    """Auto-detect KBS (period cols) vs VCI (period rows) format."""
    if _period_cols(df):
        return _facts_from_kbs(df, ticker, report_type, vnstock_report, period_from, period_to)
    return _facts_from_vci(df, ticker, report_type, vnstock_report, period_from, period_to)


# ── Main fetch ─────────────────────────────────────────────────────────────────

_REPORTS = [
    ("income_statement", "KQKD"),
    ("balance_sheet",    "CDKT"),
    ("cash_flow",        "LCTT"),
]


def fetch_finance_facts(
    ticker: str,
    report_type: Literal["standalone", "consolidated"],
    period_from: int,
    period_to: int,
    source: str = "VCI",
    show_schema: bool = False,
) -> list[dict]:
    try:
        from vnstock import Finance  # type: ignore[import]
    except ImportError:
        print("vnstock not installed. Run: pip install vnstock")
        sys.exit(1)

    f = Finance(symbol=ticker, source=source)
    all_facts: list[dict] = []

    for method_name, label in _REPORTS:
        print(f"  Fetching {label} (source={source})...")
        try:
            df = getattr(f, method_name)(period="year")
        except Exception as exc:
            print(f"  [WARN] {label} failed: {exc}")
            continue

        if df is None or (hasattr(df, "empty") and df.empty):
            print(f"  [WARN] {label} returned empty")
            continue

        if show_schema:
            print(f"\n=== {label} ({method_name}) ===")
            print(f"Shape : {df.shape}")
            print(f"Columns: {df.columns.tolist()}")
            print(df.head(5).to_string())
            continue

        try:
            facts = _parse_df(df, ticker, report_type, method_name, period_from, period_to)
            print(f"  → {len(facts)} facts")
            all_facts.extend(facts)
        except Exception as exc:
            print(f"  [WARN] Parse {label} error: {exc}")

    return all_facts


# ── DB insert ──────────────────────────────────────────────────────────────────

def insert_vnstock_facts(facts: list[dict]) -> int:
    from data.db import get_conn  # noqa: PLC0415
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO financial_facts
                    (ticker, period, report_type, metric_code, value, unit,
                     source_file, source_page, source)
                VALUES (%(ticker)s, %(period)s, %(report_type)s, %(metric_code)s,
                        %(value)s, %(unit)s, %(source_file)s, %(source_page)s, %(source)s)
                ON CONFLICT ON CONSTRAINT financial_facts_unique
                DO UPDATE SET
                    value       = EXCLUDED.value,
                    source_file = EXCLUDED.source_file
                """,
                facts,
            )
    return len(facts)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch financial statements from vnstock Finance API → Postgres")
    parser.add_argument("--ticker",       default="HPG")
    parser.add_argument("--period-from",  dest="period_from", type=int, default=2020)
    parser.add_argument("--period-to",    dest="period_to",   type=int, default=2024)
    parser.add_argument("--report-type",  dest="report_type", default="consolidated",
                        choices=["standalone", "consolidated"])
    parser.add_argument("--source",       default="VCI",  choices=["VCI", "KBS", "TCBS"])
    parser.add_argument("--dry-run",      action="store_true", help="Print only, no insert")
    parser.add_argument("--show-schema",  action="store_true", help="Print DataFrame schema and exit")
    args = parser.parse_args()

    print(f"Fetching {args.ticker} ({args.period_from}–{args.period_to}, {args.source}, {args.report_type})...")

    facts = fetch_finance_facts(
        ticker=args.ticker,
        report_type=args.report_type,
        period_from=args.period_from,
        period_to=args.period_to,
        source=args.source,
        show_schema=args.show_schema,
    )

    if args.show_schema:
        return

    print(f"\nTotal: {len(facts)} facts")

    if args.dry_run:
        shown = facts[:20]
        for f in shown:
            print(f"  {f['period']} | {f['metric_code']:35s} | {f['value']:>25,.0f} | {f['source_file']}")
        if len(facts) > 20:
            print(f"  ... ({len(facts) - 20} more facts)")
        print("--dry-run: no insert")
        return

    n = insert_vnstock_facts(facts)
    print(f"Inserted {n} rows into financial_facts (source='vnstock')")


if __name__ == "__main__":
    main()
