"""
ingest/fetch_financials.py — Fetch financial statements via vnmarket → Postgres.

Second data path (no PDF, no LLM):
  vnmarket finance.statement → financial_facts → Postgres (source='vnstock', legacy label)

Query priority: structured API data (source='vnstock') > pdf (see extract_facts.query_fact)

Schema:  python ingest/fetch_financials.py --ticker HPG --show-schema
Dry run: python ingest/fetch_financials.py --ticker HPG --dry-run
Run:     python ingest/fetch_financials.py --ticker HPG --period-from 2020 --period-to 2024
"""

from __future__ import annotations

import argparse
import collections
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


_PERIOD_RE = re.compile(r"^\d{4}(-Q[1-4]|-Năm)?$")


def _period_cols(df) -> list[str]:
    return [c for c in df.columns if _PERIOD_RE.match(str(c))]


# ── field code → metric_code mapping ───────────────────────────────────────────
# vnmarket statements are keyed by stable VCI IQ field codes (same codes for banks and
# non-banks). The codes below keep the metric_code convention the agent queries; every
# other code falls back to slugify(title_vi) from the vnmarket metric dictionary.

_FIELD_CODE_MAP: dict[str, str] = {
    # income
    "isa3":  "doanh_thu_thuan",
    "isa4":  "gia_von_hang_ban",
    "isa5":  "loi_nhuan_gop",
    "isa16": "loi_nhuan_truoc_thue",
    "isa20": "loi_nhuan_sau_thue",
    # balance
    "bsa1":  "tai_san_ngan_han",
    "bsa2":  "tien_va_tuong_duong_tien",
    "bsa23": "tai_san_dai_han",
    "bsa53": "tong_tai_san",
    "bsa54": "no_phai_tra",
    "bsa55": "no_ngan_han",
    "bsa67": "no_dai_han",
    "bsa78": "von_chu_so_huu",
    "bsa80": "von_dieu_le",
    # cash flow
    "cfa18": "dong_tien_hoat_dong",
    "cfa26": "dong_tien_dau_tu",
    "cfa34": "dong_tien_tai_chinh",
}


def _facts_from_vnmarket(
    df, titles: dict[str, str], ticker: str, report_type: str, kind: str,
    period_from: int, period_to: int, quarterly: bool = False,
) -> list[dict]:
    """vnmarket statement (rows=periods, cols=field codes) → financial_facts rows.

    Period labels: '2024' (annual) or 'Q3/2024' (quarterly), as the rest of the pipeline expects.
    Columns with no mapping and no title, or whose title is shared by several codes, are skipped.
    """
    if df is None or df.empty or "year" not in df.columns:
        raise ValueError(f"Unexpected statement layout. Columns: {getattr(df, 'columns', [])}")

    metric_of: dict[str, str] = {}
    for col in df.columns:
        code = str(col).lower()
        if code in _FIELD_CODE_MAP:
            metric_of[col] = _FIELD_CODE_MAP[code]
    slug_of = {col: slugify(titles.get(str(col).lower()) or "") for col in df.columns
               if col not in metric_of and str(col).lower() not in {"year", "quarter", "ticker", "public_date"}}
    counts = collections.Counter(slug_of.values())
    taken = set(metric_of.values())
    for col, slug in slug_of.items():
        if slug and counts[slug] == 1 and slug not in taken:   # a title shared by several codes is ambiguous: skip
            metric_of[col] = slug

    facts, seen = [], set()
    for _, row in df.iterrows():
        try:
            year, quarter = int(row["year"]), int(row.get("quarter") or 0)
        except (TypeError, ValueError):
            continue
        if not (period_from <= year <= period_to) or (quarter > 0) != quarterly:
            continue
        period = f"Q{quarter}/{year}" if quarterly else str(year)
        for col, metric_code in metric_of.items():
            try:
                fval = float(row[col])
            except (TypeError, ValueError):
                continue
            if fval == 0 or fval != fval or (period, metric_code) in seen:  # skip 0, NaN, duplicates
                continue
            seen.add((period, metric_code))
            facts.append(_make_fact(ticker, period, report_type, metric_code, fval, kind))
    return facts


def _make_fact(ticker, period, report_type, metric_code, value, kind) -> dict:
    return dict(
        ticker=ticker,
        period=str(period),
        report_type=report_type,
        metric_code=metric_code,
        value=value,
        unit="VND",
        source_file=f"vnmarket_{kind}",
        source_page=0,
        source="vnstock",   # legacy label: extract_facts.query_fact ranks structured API data first
    )


# ── Main fetch ─────────────────────────────────────────────────────────────────

_REPORTS = [
    ("income",   "income_statement", "KQKD"),
    ("balance",  "balance_sheet",    "CDKT"),
    ("cashflow", "cash_flow",        "LCTT"),
]


def fetch_finance_facts(
    ticker: str,
    report_type: Literal["standalone", "consolidated"],
    period_from: int,
    period_to: int,
    source: str = "VCI",   # kept for caller compatibility; vnmarket picks the provider itself
    show_schema: bool = False,
    quarterly: bool = False,
) -> list[dict]:
    from core.vnmarket_client import get_client

    vn = get_client()
    all_facts: list[dict] = []

    for kind, report_name, label in _REPORTS:
        print(f"  Fetching {label} (vnmarket)...")
        try:
            df = vn.finance.statement(ticker, kind=kind, period="quarter" if quarterly else "year", labels=False)
        except Exception as exc:
            print(f"  [WARN] {label} failed: {exc}")
            continue

        if df is None or df.empty:
            print(f"  [WARN] {label} returned empty")
            continue

        if show_schema:
            print(f"\n=== {label} ({kind}) ===")
            print(f"Shape : {df.shape}")
            print(f"Columns: {df.columns.tolist()}")
            print(df.head(5).to_string())
            continue

        try:
            try:
                titles = {str(r.field).lower(): r.title_vi for r in vn.finance.metrics(ticker, kind=kind).itertuples()
                          if r.field}
            except Exception:   # titles only name the unmapped codes; mapped metrics still ingest
                titles = {}
            facts = _facts_from_vnmarket(df, titles, ticker, report_type, report_name, period_from, period_to, quarterly)
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
    parser = argparse.ArgumentParser(description="Fetch financial statements via vnmarket → Postgres")
    parser.add_argument("--ticker",       default="HPG",
                        help="One ticker, a comma-separated list, or 'all' (every active security)")
    parser.add_argument("--period-from",  dest="period_from", type=int, default=2020)
    parser.add_argument("--period-to",    dest="period_to",   type=int, default=2024)
    parser.add_argument("--report-type",  dest="report_type", default="consolidated",
                        choices=["standalone", "consolidated"])
    parser.add_argument("--source",       default="VCI", choices=["VCI", "KBS"],
                        help="Ignored (vnmarket selects the provider); kept for existing callers")
    parser.add_argument("--quarterly",    action="store_true",
                        help="Fetch quarterly statements (period 'Q3/2024')")
    parser.add_argument("--dry-run",      action="store_true", help="Print only, no insert")
    parser.add_argument("--show-schema",  action="store_true", help="Print DataFrame schema and exit")
    args = parser.parse_args()

    if args.ticker.strip().lower() == "all":
        from core.tickers import get_tickers
        tickers = get_tickers()
    else:
        tickers = [t.strip().upper() for t in args.ticker.split(",") if t.strip()]

    facts: list[dict] = []
    inserted = 0
    for i, ticker in enumerate(tickers):
        print(f"Fetching {ticker} ({args.period_from}–{args.period_to}, {args.source}, "
              f"{'quarterly' if args.quarterly else 'annual'}, {args.report_type})...")
        try:
            batch = fetch_finance_facts(
                ticker=ticker,
                report_type=args.report_type,
                period_from=args.period_from,
                period_to=args.period_to,
                source=args.source,
                show_schema=args.show_schema,
                quarterly=args.quarterly,
            )
            facts.extend(batch)
            if batch and not (args.dry_run or args.show_schema):
                inserted += insert_vnstock_facts(batch)   # per ticker: one failure doesn't lose the run
        except Exception as exc:
            print(f"  [WARN] {ticker} failed: {exc}")

    if args.show_schema:
        return

    print(f"\nTotal: {len(facts)} facts")

    if args.dry_run:
        for f in facts[:20]:
            print(f"  {f['ticker']} | {f['period']} | {f['metric_code']:35s} | {f['value']:>25,.0f} | {f['source_file']}")
        if len(facts) > 20:
            print(f"  ... ({len(facts) - 20} more facts)")
        print("--dry-run: no insert")
        return

    print(f"Inserted {inserted} rows into financial_facts (source='vnstock')")


if __name__ == "__main__":
    main()
