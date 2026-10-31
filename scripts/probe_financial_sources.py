"""
scripts/probe_financial_sources.py — which source has the statements a ticker is missing? (read-only, no DB writes)

For each ticker, asks every statement source vnmarket knows and prints how many annual years / quarters each one
returns, the newest period, and whether operating cash flow (a critical field of vn-portfolio's long-term
engine) is there:

  VCI      vn.finance.statement(labels=False)     → what financial_facts (source 'vnstock') is filled from today
  KBS      vn.finance.statement fallback (labels)  → only used when VCI is down (KBS line names, not VCI codes)
  FireAnt  vn.finance.data (Y / Q)                 → fireant_financial_data; /advisor/market-data fills VCI gaps with it
  VNDirect vn.finance.item_names + items           → model types that apply + number of fiscal dates per model

Usage (inside the api container or the venv, needs network to the sources; FireAnt needs FIREANT_TOKEN or
FIREANT_EMAIL/PASSWORD):

  python scripts/probe_financial_sources.py HPG FPT PNJ           # a few tickers
  python scripts/probe_financial_sources.py --gaps 30             # 30 tickers with gaps from the DB (financial_gap_sensor)
  python scripts/probe_financial_sources.py --gaps 30 --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OCF_VCI = "cfa18"          # VCI IQ field code of operating cash flow (ingest/fetch_financials._FIELD_CODE_MAP)


def _periods(df, year_col="year", quarter_col="quarter") -> tuple[list[int], list[str]]:
    if df is None or df.empty or year_col not in df.columns:
        return [], []
    years, quarters = set(), set()
    for _, r in df.iterrows():
        y = int(r[year_col])
        q = int(r.get(quarter_col) or 0) if quarter_col in df.columns else 0
        if q in (0, 5):
            years.add(y)
        elif 1 <= q <= 4:
            quarters.add(f"{y}-Q{q}")
    return sorted(years), sorted(quarters)


def _fmt(years: list[int], quarters: list[str]) -> str:
    y = f"{len(years)} năm ({years[0]}–{years[-1]})" if years else "0 năm"
    q = f"{len(quarters)} quý (mới nhất {quarters[-1]})" if quarters else "0 quý"
    return f"{y}, {q}"


def probe_vci(vn, t: str) -> dict:
    out = {}
    try:
        inc_y = vn.finance.statement(t, kind="income", period="year", labels=False)
        cf_y = vn.finance.statement(t, kind="cashflow", period="year", labels=False)
        inc_q = vn.finance.statement(t, kind="income", period="quarter", labels=False)
        years, _ = _periods(inc_y)
        _, quarters = _periods(inc_q)
        ocf_years = sorted(int(r["year"]) for _, r in cf_y.iterrows()
                           if OCF_VCI in cf_y.columns and r.get(OCF_VCI) == r.get(OCF_VCI) and r.get(OCF_VCI)
                           and int(r.get("quarter") or 0) in (0, 5)) if cf_y is not None else []
        out = {"years": years, "quarters": quarters, "ocf_years": ocf_years}
    except Exception as exc:
        out = {"error": f"{type(exc).__name__}: {exc}"[:120]}
    return out


def probe_kbs(vn, t: str) -> dict:
    from vnmarket.sources import kbs
    try:
        y = vn.finance._x(_one(kbs.statement(t, "income", "year")))
        q = vn.finance._x(_one(kbs.statement(t, "income", "quarter")))
        years, _ = _periods(y)
        _, quarters = _periods(q)
        return {"years": years, "quarters": quarters}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}


def _one(endpoint):
    def plan():
        return (yield endpoint)
    return plan()


def probe_fireant(vn, t: str) -> dict:
    from vnmarket.sources import fireant
    if not fireant.enabled():
        return {"error": "chưa cấu hình FIREANT_TOKEN / FIREANT_EMAIL+PASSWORD"}
    try:
        y = vn.finance.data(t, "Y", 17)
        q = vn.finance.data(t, "Q", 60)
        years = sorted(int(r["year"]) for _, r in y.iterrows())
        _, quarters = _periods(q)
        ocf_years = sorted(int(r["year"]) for _, r in y.iterrows()
                           if r.get("CashflowFromOperatingActivity") == r.get("CashflowFromOperatingActivity")
                           and r.get("CashflowFromOperatingActivity") is not None)
        return {"years": years, "quarters": quarters, "ocf_years": ocf_years,
                "parent": "ParentCompanyShareholderProfitAfterTax" in y.columns}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}


VND_PLAN_MODELS = {33}         # AGM business plan (portfolio/market_data VND_ITEMS), not a statement


def probe_vnd(vn, t: str) -> dict:
    """Statement models only (plan model 33 left out); years = ANNUAL rows, quarters = QUARTER rows."""
    try:
        names = vn.finance.item_names(t)
        models = sorted({int(m) for m in names["model_type"].unique()} - VND_PLAN_MODELS)
        if not models:
            return {"error": "không có model BCTC"}
        df = vn.finance.items(t, models)
        if df.empty:
            return {"years": [], "quarters": [], "models": models}
        df = df[~df["model_type"].isin(VND_PLAN_MODELS)]
        rt = df["report_type"].fillna("").str.upper()
        years = sorted({d.year for d in df.loc[rt == "ANNUAL", "fiscal_date"]})
        quarters = sorted({f"{d.year}-Q{(d.month - 1) // 3 + 1}" for d in df.loc[rt == "QUARTER", "fiscal_date"]})
        other = sorted(set(rt) - {"ANNUAL", "QUARTER"})
        return {"years": years, "quarters": quarters, "models": models, "other_report_types": other}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}


def gap_tickers(n: int) -> list[str]:
    from core.db import get_conn
    from portfolio.financial_gaps import financial_gap_tickers
    with get_conn() as conn, conn.cursor() as cur:
        annual, quarter = financial_gap_tickers(cur, date.today())
    return list(dict.fromkeys(annual + quarter))[:n]


def _summary(rows: list[dict], tickers: list[str]) -> None:
    """Per source: tickers it can bring to ≥ 4 annual years / a quarter no older than the previous one; and how many
    tickers no source reaches 4 years for (nothing to fetch — new listings)."""
    from portfolio.financial_gaps import GAP_STALE_DAYS
    today = date.today()
    cut = today - timedelta(days=GAP_STALE_DAYS)        # vn-portfolio long_term.stale_financials

    def q_end(label):                                   # "2026-Q2" → 2026-06-30
        y, q = int(label[:4]), int(label[-1])
        return date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - timedelta(days=1)

    print(f"\n== Tổng hợp {len(tickers)} mã (đủ năm = ≥ 4 năm trong {today.year - 5}–{today.year - 1}; "
          f"quý mới = quý kết thúc từ {cut} trở đi, như engine dài hạn)")
    srcs = list(dict.fromkeys(r["source"] for r in rows))
    by = {(r["ticker"], r["source"]): r for r in rows}

    def enough(r):
        return r and not r["error"] and r["recent_years"] >= 4

    def fresh(r):
        return bool(r and not r["error"] and r["last_quarter"] and q_end(r["last_quarter"]) >= cut)

    for src in srcs:
        rs = [by.get((t, src)) for t in tickers]
        print(f"  {src:8s} đủ năm {sum(1 for r in rs if enough(r)):3d}   quý mới {sum(1 for r in rs if fresh(r)):3d}   "
              f"lỗi/không có {sum(1 for r in rs if not r or r['error']):3d}")
    only = {s: [t for t in tickers if enough(by.get((t, s))) and not enough(by.get((t, "VCI")))
                and not enough(by.get((t, "FireAnt")))] for s in srcs if s not in ("VCI", "FireAnt")}
    for s, ts in only.items():
        print(f"  chỉ {s} mới đủ năm (VCI, FireAnt không đủ): {len(ts)} {ts[:15]}")
    none = [t for t in tickers if not any(enough(by.get((t, s))) for s in srcs)]
    print(f"  không nguồn nào đủ 4 năm (mã mới / nguồn không có): {len(none)} {none[:20]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--gaps", type=int, default=0, help="take N tickers with statement gaps from the DB")
    ap.add_argument("--csv", help="also write one row per ticker × source")
    ap.add_argument("--no-vnd", action="store_true", help="skip VNDirect (slowest)")
    a = ap.parse_args()
    tickers = [t.upper() for t in a.tickers] + (gap_tickers(a.gaps) if a.gaps else [])
    if not tickers:
        ap.error("give tickers or --gaps N")

    from vnmarket import VnMarket
    rows = []
    with VnMarket() as vn:
        for t in tickers:
            print(f"\n== {t}")
            res = {"VCI": probe_vci(vn, t), "KBS": probe_kbs(vn, t), "FireAnt": probe_fireant(vn, t)}
            if not a.no_vnd:
                res["VNDirect"] = probe_vnd(vn, t)
            for src, r in res.items():
                if "error" in r:
                    line = f"lỗi: {r['error']}"
                else:
                    line = _fmt(r["years"], r["quarters"])
                    if "ocf_years" in r:
                        line += f"; OCF {len(r['ocf_years'])} năm"
                    if "parent" in r:
                        line += f"; LNST mẹ: {'có' if r['parent'] else 'không'}"
                    if src == "VNDirect":
                        line += f"; model {r.get('models')}"
                        if r.get("other_report_types"):
                            line += f"; loại khác {r['other_report_types']}"
                print(f"  {src:8s} {line}")
                recent = [y for y in r.get("years") or [] if date.today().year - 5 <= y <= date.today().year - 1]
                rows.append({"ticker": t, "source": src, "years": len(r.get("years") or []),
                             "recent_years": len(recent),
                             "first_year": (r.get("years") or [None])[0], "last_year": (r.get("years") or [None])[-1],
                             "quarters": len(r.get("quarters") or []),
                             "last_quarter": (r.get("quarters") or [None])[-1],
                             "ocf_years": len(r.get("ocf_years") or []), "error": r.get("error", "")})
    _summary(rows, tickers)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nđã ghi {a.csv}")


if __name__ == "__main__":
    main()
