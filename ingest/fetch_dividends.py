"""
ingest/fetch_dividends.py — Dividend history (cash + stock) → dividends table.

Source: vnstock VCI Company.events() filtered to dividend events. Amounts are not a stable
column across providers, so the cash/stock figure is parsed from the event title text
(`parse_dividend_text`). Rows whose text yields no figure are skipped and reported.

Usage:
    python ingest/fetch_dividends.py --tickers VNM --show-schema   # inspect raw columns first
    python ingest/fetch_dividends.py --tickers VNM,HPG --dry-run
    python ingest/fetch_dividends.py                               # all active tickers
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

PAR_VALUE_VND = 10_000   # "tỷ lệ 10%" bằng tiền = 10% × mệnh giá 10.000đ = 1.000đ/cp

_NUM = r"\d+(?:[.,]\d+)?"
_CASH_VND_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?:đồng|đ|vnđ|vnd)\s*/\s*(?:cp|cổ phiếu|cổ phần)", re.IGNORECASE)
_CASH_PCT_RES = (
    re.compile(rf"bằng tiền(?: mặt)?[^%\d]{{0,50}}?({_NUM})\s*%", re.IGNORECASE),
    re.compile(rf"({_NUM})\s*%\s*(?:\(?\s*)?bằng tiền", re.IGNORECASE),
)
_STOCK_PCT_RES = (
    re.compile(rf"bằng cổ phiếu[^%\d]{{0,50}}?({_NUM})\s*%", re.IGNORECASE),
    re.compile(rf"({_NUM})\s*%\s*(?:\(?\s*)?bằng cổ phiếu", re.IGNORECASE),
    re.compile(rf"(?:thưởng|phát hành)[^%\d:]{{0,40}}?({_NUM})\s*%", re.IGNORECASE),
)
_STOCK_RATIO_RE = re.compile(r"(\d+)\s*:\s*(\d+)")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_dividend_text(text: str) -> dict:
    """Extract {'cash_per_share': VND/cp|None, 'stock_ratio_pct': %|None} from a Vietnamese event title.

    Cash: '1.500 đồng/cp' or 'bằng tiền ... 10%' (×10.000đ par). Stock: 'bằng cổ phiếu ... 20%',
    'thưởng ... 20%' or ratio '100:20' (only when the text mentions cổ phiếu and no cash figure).
    """
    t = text or ""
    cash = None
    m = _CASH_VND_RE.search(t)
    if m:
        cash = float(re.sub(r"[.,]", "", m.group(1)))
    else:
        for rx in _CASH_PCT_RES:
            m = rx.search(t)
            if m:
                cash = round(_num(m.group(1)) / 100 * PAR_VALUE_VND, 2)
                break
    stock = None
    for rx in _STOCK_PCT_RES:
        m = rx.search(t)
        if m:
            stock = _num(m.group(1))
            break
    if stock is None and cash is None and re.search(r"cổ phiếu", t, re.IGNORECASE):
        m = _STOCK_RATIO_RE.search(t)
        if m and int(m.group(1)) > 0:
            stock = round(int(m.group(2)) / int(m.group(1)) * 100, 2)
    return {"cash_per_share": cash, "stock_ratio_pct": stock}


def _first(row, *cols):
    for c in cols:
        v = row.get(c)
        if v is not None and str(v) not in ("", "nan", "NaT", "None"):
            return v
    return None


def _to_date(v):
    if v is None:
        return None
    try:
        from datetime import date
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def rows_from_events(ticker: str, df) -> tuple[list[dict], int]:
    """vnstock events frame → dividend rows; second value = dividend events with no parsable figure."""
    out: list[dict] = []
    skipped = 0
    for _, r in df.iterrows():
        row = r.to_dict()
        code = str(_first(row, "event_list_code", "event_code", "event_type") or "").upper()
        title = " ".join(str(_first(row, k) or "") for k in ("event_title_vi", "event_title", "event_name_vi", "event_name", "event_desc", "description", "note")).strip()
        is_div = code.startswith("DIV") or re.search(r"cổ tức|trả cổ phiếu|thưởng cổ phiếu", title, re.IGNORECASE) \
            or (code == "ISS" and re.search(r"cổ phiếu thưởng", title, re.IGNORECASE))
        if not is_div:
            continue
        ex = _to_date(_first(row, "exright_date", "ex_date", "record_date"))
        if ex is None:
            skipped += 1
            continue
        parsed = parse_dividend_text(title)
        vps = _first(row, "value_per_share")   # VCI: cash VND per share on DIV events
        if code.startswith("DIV") and vps is not None and float(vps) > 0:
            parsed["cash_per_share"] = float(vps)
        if parsed["cash_per_share"] is None and parsed["stock_ratio_pct"] is None:
            skipped += 1
            continue
        common = dict(ticker=ticker, ex_date=ex, record_date=_to_date(_first(row, "record_date")),
                      payment_date=_to_date(_first(row, "payout_date", "issue_date", "payment_date")),
                      note=title[:500], source="vnstock_vci")
        if parsed["cash_per_share"] is not None:
            out.append({**common, "kind": "cash", "cash_per_share": parsed["cash_per_share"], "stock_ratio_pct": None})
        if parsed["stock_ratio_pct"] is not None:
            out.append({**common, "kind": "stock", "cash_per_share": None, "stock_ratio_pct": parsed["stock_ratio_pct"]})
    return out, skipped


_UPSERT = """
INSERT INTO dividends (ticker, kind, ex_date, record_date, payment_date,
                       cash_per_share, stock_ratio_pct, note, source)
VALUES (%(ticker)s, %(kind)s, %(ex_date)s, %(record_date)s, %(payment_date)s,
        %(cash_per_share)s, %(stock_ratio_pct)s, %(note)s, %(source)s)
ON CONFLICT (ticker, ex_date, kind) DO UPDATE SET
    record_date = EXCLUDED.record_date, payment_date = EXCLUDED.payment_date,
    cash_per_share = EXCLUDED.cash_per_share, stock_ratio_pct = EXCLUDED.stock_ratio_pct,
    note = EXCLUDED.note, source = EXCLUDED.source, fetched_at = NOW()
"""


def upsert_dividends(rows: list[dict]) -> int:
    if not rows:
        return 0
    from core.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, rows)
    return len(rows)


def fetch_events_df(ticker: str):
    from vnstock import Company
    try:
        return Company(symbol=ticker, source="VCI").events()
    except Exception:   # transient read timeout from iq.vietcap.com.vn: one retry before the caller counts a failure
        time.sleep(5)
        return Company(symbol=ticker, source="VCI").events()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch dividend history → dividends")
    parser.add_argument("--tickers", default="", help="Comma-separated; empty = all active securities")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-schema", action="store_true", help="Print raw event columns/rows and exit")
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        from core.tickers import get_tickers
        tickers = get_tickers()

    total, failed = 0, []
    for t in tickers:
        try:
            df = fetch_events_df(t)
            if df is None or df.empty:
                print(f"  {t}: no events")
                continue
            if args.show_schema:
                print(f"{t} columns: {df.columns.tolist()}")
                print(df.head(8).to_string())
                continue
            rows, skipped = rows_from_events(t, df)
            n = len(rows) if args.dry_run else upsert_dividends(rows)
            total += n
            print(f"  {t}: {n} dividend rows" + (f" ({skipped} events skipped: no date/figure)" if skipped else ""))
            if args.dry_run:
                for r in rows[:5]:
                    print(f"    {r['ex_date']} {r['kind']} cash={r['cash_per_share']} stock%={r['stock_ratio_pct']} | {r['note'][:80]}")
        except Exception as exc:
            print(f"  {t}: FAILED — {exc}", file=sys.stderr)
            failed.append(t)
        time.sleep(3.2)
    print(f"\ndividends: {total} rows{' (dry-run)' if args.dry_run else ''}; failed: {len(failed)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
