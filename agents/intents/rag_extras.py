"""
agents/intents/rag_extras.py — structured supplements for rag_qa questions.

rag_qa.retrieve_only() answers from text-to-SQL + document chunks, which cannot answer:
  • dividend history + yield                → dividends (corporate_events as fallback)
  • ROE / EPS per year                      → financial_facts ratio_* (published), else derived
  • quarterly figures (Q1/2025 ...)         → financial_facts periods 'Qn/YYYY' (fetch_financials --quarterly)

gather_extras() returns labelled text blocks (no LLM) appended to the rag_qa context.
"""

from __future__ import annotations

import re
import sys

from agents.intent_rules import _DIVIDEND_RE, _FIN_ITEM_RE, _QUARTER_RE, _TOPIC_PATTERNS, _YEARLY_METRIC_RE

_PROFIT_CODES = ("lai_lo_thuan_sau_thue", "lailo_thuan_sau_thue", "loi_nhuan_sau_thue")
_EQUITY_CODE = "von_chu_so_huu"


def _rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as exc:
        sys.stderr.write(f"[rag_extras] query failed: {exc}\n")
        return None


# ── market cap ────────────────────────────────────────────────────────────────

def _market_cap(ticker: str) -> str:
    """Market cap = shares outstanding × latest close, with the inputs shown."""
    sh = _rows("SELECT listed_shares, company_name FROM securities WHERE ticker = %s", (ticker,))
    px = _latest_close_vnd(ticker)
    shares = int(sh[0][0]) if sh and sh[0][0] else 0
    if not shares:
        return (f"[VỐN HÓA — {ticker}]\nChưa có số cổ phiếu lưu hành của {ticker} trong cơ sở dữ liệu "
                "(securities.listed_shares = 0) nên không tính được vốn hóa; không được ước lượng.")
    if not px:
        return f"[VỐN HÓA — {ticker}]\nCó số cổ phiếu ({shares:,}) nhưng chưa có giá đóng cửa để tính vốn hóa."
    mcap = shares * px[0] / 1e9
    return (f"[VỐN HÓA — {ticker}]\nVốn hóa ≈ {mcap:,.0f} tỷ VND "
            f"(= {shares:,} cổ phiếu lưu hành × giá đóng cửa {px[0]:,.0f} đ, phiên {px[1]}).")


# ── dividends ─────────────────────────────────────────────────────────────────

def _latest_close_vnd(ticker: str) -> tuple[float, object] | None:
    r = _rows("SELECT close, date FROM ohlcv_daily WHERE ticker = %s ORDER BY date DESC LIMIT 1", (ticker,))
    return (float(r[0][0]) * 1000.0, r[0][1]) if r else None   # ohlcv_daily stores nghìn đồng


def _dividends_from_table(ticker: str) -> str | None:
    """Dividend history + trailing-12-month yield from the `dividends` table; None when empty."""
    rows = _rows(
        "SELECT ex_date, kind, cash_per_share, stock_ratio_pct, payment_date FROM dividends "
        "WHERE ticker = %s AND ex_date >= CURRENT_DATE - INTERVAL '6 years' ORDER BY ex_date DESC",
        (ticker,),
    )
    if not rows:
        return None
    lines: list[str] = []
    by_year: dict[int, float] = {}
    for ex, kind, cash, stock, pay in rows:
        if kind == "cash" and cash is not None:
            lines.append(f"- GDKHQ {ex}: tiền mặt {float(cash):,.0f} đồng/cp" + (f" (thanh toán {pay})" if pay else ""))
            by_year[ex.year] = by_year.get(ex.year, 0.0) + float(cash)
        elif kind == "stock" and stock is not None:
            lines.append(f"- GDKHQ {ex}: cổ phiếu tỷ lệ {float(stock):g}% (100 cp nhận thêm {float(stock):g} cp)")
    out = [f"[CỔ TỨC — {ticker}]"] + lines
    if by_year:
        out.append("Tổng cổ tức tiền mặt theo năm GDKHQ: " +
                   "; ".join(f"{y}: {v:,.0f} đ/cp" for y, v in sorted(by_year.items(), reverse=True)))
    px = _latest_close_vnd(ticker)
    ttm = _rows("SELECT COALESCE(SUM(cash_per_share), 0) FROM dividends WHERE ticker = %s AND kind = 'cash' "
                "AND ex_date > CURRENT_DATE - INTERVAL '365 days'", (ticker,))
    if px and ttm and float(ttm[0][0]) > 0:
        cash_ttm = float(ttm[0][0])
        out.append(f"Tỷ suất cổ tức 12 tháng gần nhất = {cash_ttm:,.0f} đ/cp ÷ giá {px[0]:,.0f} đ (đóng cửa {px[1]}) "
                   f"= {cash_ttm / px[0] * 100:.2f}%.")
    elif px:
        out.append("Không có đợt cổ tức tiền mặt nào trong 12 tháng gần nhất trong dữ liệu "
                   "→ tỷ suất cổ tức 12 tháng = 0%.")
    return "\n".join(out)


def _dividends(ticker: str) -> str:
    table = _dividends_from_table(ticker)
    if table:
        return table
    try:
        from tools.events_views import get_corporate_events
        r = get_corporate_events(ticker=ticker, days_ahead=365, days_back=365 * 5)
    except Exception:
        r = None
    if not r or r.status != "ok" or not r.data:
        return (
            f"[CỔ TỨC / SỰ KIỆN QUYỀN — {ticker}]\n"
            "Cơ sở dữ liệu chưa ghi nhận sự kiện cổ tức/quyền nào của mã này trong 5 năm gần đây "
            "(có thể do chưa được thu thập, không đồng nghĩa công ty chưa chi trả)."
        )
    lines = []
    for e in r.data:
        ratio = f", tỷ lệ {e['ratio']}%" if e.get("ratio") else ""
        note = f" — {e['note']}" if e.get("note") else ""
        lines.append(f"- {e['ex_date']}: {e['event_type']}{ratio}{note}")
    return f"[CỔ TỨC / SỰ KIỆN QUYỀN — {ticker}]\n" + "\n".join(lines)


# ── ROE / EPS per year ────────────────────────────────────────────────────────

def _published_yearly(ticker: str, years: list[str]) -> str | None:
    rows = _rows(
        "SELECT period, metric_code, value FROM financial_facts "
        "WHERE ticker = %s AND metric_code IN ('ratio_roe_pct', 'ratio_eps') AND period ~ '^[0-9]{4}$' "
        "ORDER BY period",
        (ticker,),
    )
    pub: dict[str, dict[str, float]] = {}
    for period, code, val in rows or []:
        pub.setdefault(period, {})["roe" if code == "ratio_roe_pct" else "eps"] = float(val)
    lines = []
    for period in sorted(pub):
        if years and period not in years:
            continue
        d = pub[period]
        parts = ([f"ROE {d['roe']:.1f}%"] if "roe" in d else []) + ([f"EPS {d['eps']:,.0f} đ"] if "eps" in d else [])
        if parts:
            lines.append(f"- {period}: " + "; ".join(parts))
    if not lines:
        return None
    return (f"[ROE/EPS THEO NĂM — {ticker}]\n" + "\n".join(lines) +
            "\nNguồn: chỉ số công bố theo từng năm tài chính (vnstock/KBS); EPS là EPS của năm đó, "
            "chưa điều chỉnh cho cổ phiếu thưởng/tách cổ phiếu về sau.")


def _yearly(ticker: str, years: list[str]) -> str:
    """ROE/EPS per fiscal year: published ratios first, derived estimate as fallback."""
    published = _published_yearly(ticker, years)
    if published:
        return published

    rows = _rows(
        "SELECT period, metric_code, MAX(value) FROM financial_facts "
        "WHERE ticker = %s AND metric_code = ANY(%s) AND period ~ '^[0-9]{4}$' "
        "GROUP BY period, metric_code ORDER BY period",
        (ticker, list(_PROFIT_CODES) + [_EQUITY_CODE]),
    )
    if not rows:
        return f"[ROE/EPS THEO NĂM — {ticker}]\nKhông có dữ liệu báo cáo năm trong cơ sở dữ liệu."
    by_period: dict[str, dict[str, float]] = {}
    for period, code, val in rows:
        d = by_period.setdefault(period, {})
        key = "equity" if code == _EQUITY_CODE else "profit"
        d.setdefault(key, float(val))
    sh = _rows("SELECT listed_shares FROM securities WHERE ticker = %s", (ticker,))
    shares = float(sh[0][0]) if sh and sh[0][0] else None
    lines = []
    for period in sorted(by_period):
        if years and period not in years:
            continue
        d = by_period[period]
        profit, equity = d.get("profit"), d.get("equity")
        parts = []
        if profit is not None:
            parts.append(f"LNST {profit / 1e9:,.0f} tỷ")
        if profit is not None and equity:
            parts.append(f"ROE {profit / equity * 100:.1f}% (LNST/VCSH cuối kỳ)")
        if profit is not None and shares:
            parts.append(f"EPS ≈ {profit / shares:,.0f} VND (LNST/số CP niêm yết hiện tại — ước tính)")
        if parts:
            lines.append(f"- {period}: " + "; ".join(parts))
    if not lines:
        have = ", ".join(sorted(by_period))
        return f"[ROE/EPS THEO NĂM — {ticker}]\nKhông có dữ liệu cho năm được hỏi. Các năm có dữ liệu: {have}."
    return (
        f"[ROE/EPS THEO NĂM — {ticker}]\n" + "\n".join(lines) +
        "\nLưu ý: ROE tính trên vốn chủ sở hữu cuối kỳ (không phải bình quân); EPS là ước tính, không phải EPS công bố."
    )


# ── quarterly ─────────────────────────────────────────────────────────────────

def _quarter_key(period: str) -> tuple[int, int]:
    q, y = period[1:].split("/")          # 'Q3/2025' → (2025, 3)
    return int(y), int(q)


def _quarterly(ticker: str) -> str:
    """Latest quarters of revenue / net profit from financial_facts periods 'Qn/YYYY' (+ YoY)."""
    rows = _rows(
        "SELECT period, metric_code, MAX(value) FROM financial_facts "
        "WHERE ticker = %s AND period ~ '^Q[1-4]/[0-9]{4}$' AND metric_code = ANY(%s) "
        "GROUP BY period, metric_code",
        (ticker, ["doanh_thu_thuan", *_PROFIT_CODES]),
    )
    if not rows:
        return (
            f"[BÁO CÁO THEO QUÝ — {ticker}]\n"
            "Cơ sở dữ liệu chưa có báo cáo theo quý của mã này (chỉ có kỳ năm). "
            "Không được suy đoán số liệu quý từ số liệu năm; hãy nói rõ giới hạn này cho người dùng."
        )
    by_q: dict[str, dict[str, float]] = {}
    for period, code, val in rows:
        d = by_q.setdefault(period, {})
        d.setdefault("revenue" if code == "doanh_thu_thuan" else "profit", float(val))
    latest = sorted(by_q, key=_quarter_key, reverse=True)[:4]

    def yoy(period: str, key: str) -> str:
        y, q = _quarter_key(period)
        prev = by_q.get(f"Q{q}/{y - 1}", {}).get(key)
        cur = by_q[period].get(key)
        if prev and cur is not None and prev > 0:
            return f" ({(cur - prev) / prev * 100:+.1f}% so với cùng kỳ Q{q}/{y - 1})"
        return ""

    lines = []
    for p in latest:
        d = by_q[p]
        parts = []
        if "revenue" in d:
            parts.append(f"doanh thu thuần {d['revenue'] / 1e9:,.0f} tỷ{yoy(p, 'revenue')}")
        if "profit" in d:
            parts.append(f"LNST {d['profit'] / 1e9:,.0f} tỷ{yoy(p, 'profit')}")
        lines.append(f"- {p}: " + "; ".join(parts))
    return (f"[BÁO CÁO THEO QUÝ — {ticker}] (quý gần nhất có dữ liệu: {latest[0]})\n" + "\n".join(lines) +
            "\nSố liệu quý là số phát sinh trong quý (hợp nhất).")


def gather_extras(ticker: str | None, query: str) -> str:
    """Extra labelled blocks for a rag_qa question; '' when nothing applies."""
    if not ticker:
        return ""
    q = query or ""
    blocks: list[str] = []
    if _TOPIC_PATTERNS["marketcap"].search(q):
        blocks.append(_market_cap(ticker))
    if _DIVIDEND_RE.search(q):
        blocks.append(_dividends(ticker))
    if _QUARTER_RE.search(q) and _FIN_ITEM_RE.search(q):
        blocks.append(_quarterly(ticker))
    if _YEARLY_METRIC_RE.search(q):
        blocks.append(_yearly(ticker, re.findall(r"\b(20\d{2})\b", q)))
    return "\n\n".join(blocks)
