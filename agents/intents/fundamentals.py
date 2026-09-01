"""
agents/intents/fundamentals.py — Nhóm 3: Cơ bản & Định giá.

Fetches real valuation metrics (P/E, P/B, ROE, EPS) via vnstock KBS source,
pre-computes rankings in Python, injects factual statements into LLM prompt.
LLM only narrates — never re-derives comparisons from scratch.
Falls back to rag/qa.py for BCTC-specific questions.
"""

from __future__ import annotations

import math
import time as _time
from typing import Optional

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking, extract_slot
from core.tickers import get_sector_peers


# ── Data fetching ─────────────────────────────────────────────────────────────

# In-process TTL cache — L1 guard; Postgres is L2; live vnstock is fallback
_VALUATION_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600        # 1 hour in-process
_STALE_THRESHOLD = 172800  # 48 hours — treat Postgres row as stale beyond this


def _row_to_dict(ticker: str, row) -> dict:
    """Convert a Postgres stock_ratios row (tuple) to valuation dict."""
    (pe, pb, roe_pct, roa_pct, eps,
     gross_margin_pct, net_margin_pct,
     revenue_growth_pct, earnings_growth_pct,
     de_ratio, ev_ebitda) = row
    roe_f = float(roe_pct) if roe_pct is not None else None
    return {
        "ticker":             ticker,
        "pe":                 float(pe)  if pe  is not None else None,
        "pb":                 float(pb)  if pb  is not None else None,
        "roe":                roe_f / 100 if roe_f is not None else None,
        "roe_pct":            roe_f,
        "roa_pct":            float(roa_pct) if roa_pct is not None else None,
        "eps":                float(eps) if eps is not None else None,
        "price":              None,
        "gross_margin_pct":   float(gross_margin_pct)    if gross_margin_pct    is not None else None,
        "net_margin_pct":     float(net_margin_pct)      if net_margin_pct      is not None else None,
        "revenue_growth_pct": float(revenue_growth_pct)  if revenue_growth_pct  is not None else None,
        "earnings_growth_pct": float(earnings_growth_pct) if earnings_growth_pct is not None else None,
        "de_ratio":           float(de_ratio)   if de_ratio   is not None else None,
        "op_cashflow":        None,
        "fcf":                None,
        "net_income":         None,
        "ev_ebitda":          float(ev_ebitda) if ev_ebitda is not None else None,
    }


def _fetch_from_db(ticker: str) -> dict | None:
    """Read latest ratios from Postgres stock_ratios. Returns None if missing or stale."""
    try:
        from core.db import get_conn
        import datetime
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pe, pb, roe_pct, roa_pct, eps,
                           gross_margin_pct, net_margin_pct,
                           revenue_growth_pct, earnings_growth_pct,
                           de_ratio, ev_ebitda, fetched_at
                    FROM stock_ratios WHERE ticker = %s
                    """,
                    (ticker,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        fetched_at = row[-1]  # last element
        age = (_time.time() - fetched_at.timestamp()) if fetched_at else _STALE_THRESHOLD + 1
        if age > _STALE_THRESHOLD:
            return None
        return _row_to_dict(ticker, row[:-1])
    except Exception:
        return None


def _fetch_from_vnstock(ticker: str) -> dict | None:
    """Live vnstock KBS call. Returns None on any error."""
    def _empty() -> dict:
        return {
            "ticker": ticker, "pe": None, "pb": None, "roe": None,
            "roe_pct": None, "roa_pct": None, "eps": None, "price": None,
            "gross_margin_pct": None, "net_margin_pct": None,
            "revenue_growth_pct": None, "earnings_growth_pct": None,
            "de_ratio": None, "op_cashflow": None, "fcf": None,
            "net_income": None, "ev_ebitda": None,
        }
    try:
        from vnstock.api.financial import Finance as _VnFinance
        df = _VnFinance(symbol=ticker, source='KBS').ratio(period='year', lang='en')
        df = df.set_index('item_id').drop(columns=['item'], errors='ignore')
        latest = df.iloc[:, 0]

        def _get(key) -> float | None:
            v = latest.get(key)
            if v is None:
                return None
            try:
                f = float(v)
                return None if math.isnan(f) else f
            except (TypeError, ValueError):
                return None

        roe_pct = _get('roe')
        de_pct  = _get('debt_to_equity')
        return {
            "ticker":             ticker,
            "pe":                 _get('pe_ratio'),
            "pb":                 _get('pb_ratio'),
            "roe":                roe_pct / 100 if roe_pct is not None else None,
            "roe_pct":            roe_pct,
            "roa_pct":            _get('roa'),
            "eps":                _get('trailing_eps'),
            "price":              None,
            "gross_margin_pct":   _get('gross_margin'),
            "net_margin_pct":     _get('net_margin'),
            "revenue_growth_pct": _get('net_revenue'),
            "earnings_growth_pct": _get('profit_after_tax_for_shareholders_of_the_parent_company'),
            "de_ratio":           de_pct / 100 if de_pct is not None else None,
            "op_cashflow":        None,
            "fcf":                None,
            "net_income":         None,
            "ev_ebitda":          _get('ev_ebitda'),
        }
    except Exception:
        return _empty()


def _fetch_valuation(ticker: str) -> dict:
    # L1: in-process cache
    now = _time.time()
    if ticker in _VALUATION_CACHE:
        ts, cached = _VALUATION_CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return cached

    # L2: Postgres (Dagster pre-fetches daily)
    result = _fetch_from_db(ticker)

    # L3: live vnstock fallback (hits rate limit only when Postgres missing/stale)
    if result is None:
        result = _fetch_from_vnstock(ticker)

    _VALUATION_CACHE[ticker] = (now, result)
    return result


def _na(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _fmt(v, fmt=".2f", suffix="") -> str:
    if _na(v):
        return "N/A"
    try:
        return f"{v:{fmt}}{suffix}"
    except Exception:
        return "N/A"


# ── Pre-computed ranking ───────────────────────────────────────────────────────

def _rank_text(value, all_values: list, metric: str, higher_is_better: bool = True, subject: str | None = None) -> str:
    """Return a factual ranking sentence. Never lets LLM infer order."""
    valid = sorted(
        [(t, v) for t, v in all_values if not _na(v)],
        key=lambda x: x[1],
        reverse=higher_is_better,
    )
    if not valid or _na(value):
        return ""
    rank = next((i + 1 for i, (_, v) in enumerate(valid) if abs(v - value) < 1e-9), None)
    n = len(valid)
    if rank is None:
        return ""
    # Build peer comparison text — exclude the subject ticker from the "equal" list
    above = [(t, v) for t, v in valid if v > value and not _na(v)]
    below = [(t, v) for t, v in valid if v < value and not _na(v)]
    equal = [(t, v) for t, v in valid if abs(v - value) < 1e-9 and t != subject]

    parts: list[str] = []
    if above and higher_is_better:
        parts.append(f"thấp hơn {', '.join(f'{t}({v:.1f})' for t,v in above[:3])}")
    elif above and not higher_is_better:
        parts.append(f"cao hơn {', '.join(f'{t}({v:.1f})' for t,v in above[:3])}")
    if below and higher_is_better:
        parts.append(f"cao hơn {', '.join(f'{t}({v:.1f})' for t,v in below[:3])}")
    elif below and not higher_is_better:
        parts.append(f"thấp hơn {', '.join(f'{t}({v:.1f})' for t,v in below[:3])}")
    if equal:
        parts.append(f"tương đương {', '.join(t for t,_ in equal[:2])}")

    peer_str = "; ".join(parts) if parts else ""
    return f"Xếp hạng {rank}/{n} trong nhóm{': ' + peer_str if peer_str else '.'}"


def _build_analysis(ticker: str, rows: list[dict]) -> str:
    """Pre-compute all factual comparisons. Return as structured text for LLM prompt."""
    target = next((r for r in rows if r["ticker"] == ticker), None)
    if not target:
        return "Không lấy được dữ liệu."

    pe_pairs  = [(r["ticker"], r["pe"])      for r in rows if not _na(r["pe"])]
    pb_pairs  = [(r["ticker"], r["pb"])      for r in rows if not _na(r["pb"])]
    roe_pairs = [(r["ticker"], r["roe_pct"]) for r in rows if not _na(r["roe_pct"])]
    eps_pairs = [(r["ticker"], r["eps"])     for r in rows if not _na(r["eps"])]

    avg_pe  = sum(v for _, v in pe_pairs)  / len(pe_pairs)  if pe_pairs  else None
    avg_roe = sum(v for _, v in roe_pairs) / len(roe_pairs) if roe_pairs else None

    lines: list[str] = []

    # ── Table ────────────────────────────────────────────────────────────────
    lines.append("### Bảng dữ liệu thực tế")
    lines.append("| Mã | P/E | P/B | ROE | EPS |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        mark = " ◀" if r["ticker"] == ticker else ""
        lines.append(
            f"| {r['ticker']}{mark} "
            f"| {_fmt(r['pe'], '.1f')} "
            f"| {_fmt(r['pb'], '.2f')} "
            f"| {_fmt(r['roe_pct'], '.1f', '%')} "
            f"| {_fmt(r['eps'], ',.0f')} |"
        )
    if avg_pe:
        lines.append(f"| **TB ngành** | **{avg_pe:.1f}** | - | {_fmt(avg_roe, '.1f', '%') if avg_roe else '-'} | - |")

    lines.append("")
    lines.append("### Phân tích thực tế đã tính sẵn (DÙNG NGUYÊN CÁC CON SỐ NÀY)")

    # ── Growth ───────────────────────────────────────────────────────────────
    if not _na(target.get("revenue_growth_pct")):
        g = target["revenue_growth_pct"]
        tag = "TĂNG MẠNH" if g > 20 else ("TĂNG" if g > 5 else ("GIẢM" if g < 0 else "TĂNG NHẸ"))
        lines.append(f"Tăng trưởng Doanh thu YoY: {g:.1f}% — {tag}")
    if not _na(target.get("earnings_growth_pct")):
        g = target["earnings_growth_pct"]
        tag = "TĂNG MẠNH" if g > 20 else ("TĂNG" if g > 5 else ("GIẢM" if g < 0 else "TĂNG NHẸ"))
        lines.append(f"Tăng trưởng LNST YoY: {g:.1f}% — {tag}")

    # ── Margins ──────────────────────────────────────────────────────────────
    if not _na(target.get("gross_margin_pct")):
        lines.append(f"Biên lợi nhuận gộp: {target['gross_margin_pct']:.1f}%")
    if not _na(target.get("net_margin_pct")):
        lines.append(f"Biên lợi nhuận ròng: {target['net_margin_pct']:.1f}%")

    # ── Financial health ─────────────────────────────────────────────────────
    if not _na(target.get("de_ratio")):
        de = target["de_ratio"]
        tag = "CAO (rủi ro đòn bẩy)" if de > 2 else ("VỪA PHẢI" if de > 0.5 else "THẤP (an toàn)")
        lines.append(f"D/E Ratio: {de:.2f}x — {tag}")
    if not _na(target.get("roa_pct")):
        lines.append(f"ROA: {target['roa_pct']:.1f}%")
    if not _na(target.get("op_cashflow")) and not _na(target.get("net_income")):
        cf = target["op_cashflow"]
        ni = target["net_income"]
        if ni and ni != 0:
            ratio = cf / ni
            tag = "CHẤT LƯỢNG CAO (CFO > LNST)" if ratio > 1 else "CẦN CHÚ Ý (CFO < LNST)"
            lines.append(f"CFO/LNST: {ratio:.2f}x — {tag}")
    if not _na(target.get("ev_ebitda")):
        lines.append(f"EV/EBITDA: {target['ev_ebitda']:.1f}x")

    # ── P/E comparison ───────────────────────────────────────────────────────
    if not _na(target["pe"]) and avg_pe:
        premium = (target["pe"] - avg_pe) / avg_pe * 100
        if premium > 5:
            pe_verdict = f"P/E {target['pe']:.1f}x PREMIUM +{premium:.0f}% so với trung bình ngành {avg_pe:.1f}x"
        elif premium < -5:
            pe_verdict = f"P/E {target['pe']:.1f}x DISCOUNT {premium:.0f}% so với trung bình ngành {avg_pe:.1f}x"
        else:
            pe_verdict = f"P/E {target['pe']:.1f}x NGANG BẰNG trung bình ngành {avg_pe:.1f}x"
        lines.append(f"P/E: {pe_verdict}")
        rank_pe = _rank_text(target["pe"], pe_pairs, "P/E", higher_is_better=False, subject=ticker)
        if rank_pe:
            lines.append(f"  → {rank_pe}")

    # ── ROE comparison ───────────────────────────────────────────────────────
    if not _na(target["roe_pct"]) and avg_roe:
        diff_roe = target["roe_pct"] - avg_roe
        if diff_roe > 1:
            roe_verdict = f"ROE {target['roe_pct']:.1f}% CAO HƠN trung bình ngành {avg_roe:.1f}% (+{diff_roe:.1f}pp)"
        elif diff_roe < -1:
            roe_verdict = f"ROE {target['roe_pct']:.1f}% THẤP HƠN trung bình ngành {avg_roe:.1f}% ({diff_roe:.1f}pp)"
        else:
            roe_verdict = f"ROE {target['roe_pct']:.1f}% TƯƠNG ĐƯƠNG trung bình ngành {avg_roe:.1f}%"
        lines.append(f"ROE: {roe_verdict}")
        rank_roe = _rank_text(target["roe_pct"], roe_pairs, "ROE", higher_is_better=True, subject=ticker)
        if rank_roe:
            lines.append(f"  → {rank_roe}")

    # ── EPS ranking ──────────────────────────────────────────────────────────
    if not _na(target["eps"]) and eps_pairs:
        sorted_eps = sorted(eps_pairs, key=lambda x: x[1], reverse=True)
        rank_n = next((i+1 for i,(t,_) in enumerate(sorted_eps) if t == ticker), None)
        higher_eps = [(t, v) for t, v in sorted_eps if v > target["eps"]]
        lower_eps  = [(t, v) for t, v in sorted_eps if v < target["eps"]]
        parts = []
        if higher_eps:
            parts.append(f"thấp hơn {', '.join(f'{t}({v:,.0f})' for t,v in higher_eps[:2])}")
        if lower_eps:
            parts.append(f"cao hơn {', '.join(f'{t}({v:,.0f})' for t,v in lower_eps[:2])}")
        eps_detail = "; ".join(parts) if parts else "duy nhất trong nhóm"
        lines.append(f"EPS: {target['eps']:,.0f} — xếp {rank_n}/{len(eps_pairs)} ({eps_detail})")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

import re as _re
_TICKER_RE_FUND = _re.compile(r'\b([A-Z]{2,5})\b')
_FUND_STOPWORDS = frozenset({"VE", "VA", "LA", "CO", "DE", "VS", "ROE", "ROA", "EPS", "PE", "PB"})


def _extract_tickers_from_query(query: str) -> list[str]:
    """Return VN tickers explicitly mentioned in query (uppercase, deduped, filtered)."""
    hits = list(dict.fromkeys(  # preserve order, dedupe
        t for t in _TICKER_RE_FUND.findall(query.upper())
        if t not in _FUND_STOPWORDS
    ))
    return hits


def _is_sector_comparison(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in [
        "so với ngành", "so với các ngân hàng", "so với peer", "ngành ngân hàng",
        "toàn ngành", "so sánh", "trung bình ngành", "so với thị trường",
        "so với", "vs ngành", "p/e", "p/b", "roe", "eps",
    ])


_SYSTEM = (
    "Bạn là chuyên gia phân tích định giá chứng khoán Việt Nam. "
    "TUYỆT ĐỐI KHÔNG tự tính lại thứ hạng — dùng nguyên kết quả đã cho. "
    "Số đã cho là sự thật, viết dứt khoát — không dùng 'có thể', 'dường như'. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 7 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _assemble_fund_report(
    ticker: str,
    data_table: str,
    tang_truong: str,
    bien_ln: str,
    hieu_qua: str,
    suc_khoe: str,
    dinh_gia: str,
    moat: str,
    nhan_dinh: str,
) -> str:
    return (
        f"# Phân tích Cơ bản & Định giá {ticker}\n\n"
        f"{data_table}\n\n"
        f"## Tăng trưởng (Doanh thu & LNST YoY)\n{tang_truong}\n\n"
        f"## Biên lợi nhuận\n{bien_ln}\n\n"
        f"## Hiệu quả vốn (ROE, ROA)\n{hieu_qua}\n\n"
        f"## Sức khỏe tài chính (D/E, CFO vs LNST)\n{suc_khoe}\n\n"
        f"## Định giá (P/E, P/B, EV/EBITDA)\n{dinh_gia}\n\n"
        f"## Lợi thế cạnh tranh (Moat)\n{moat}\n\n"
        f"## Nhận định tổng thể\n{nhan_dinh}\n\n"
        f"[Nguồn: vnstock/KBS]"
    )


def _extract_data_table(analysis: str) -> str:
    """Extract the peer comparison table block from pre-computed analysis."""
    lines = analysis.splitlines()
    table_lines: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("### Bảng dữ liệu thực tế"):
            in_table = True
        if in_table:
            if line.startswith("### Phân tích thực tế"):
                break
            table_lines.append(line)
    return "\n".join(table_lines).strip() if table_lines else ""


def gather_data(ticker: str | None, query: str) -> str:
    """Fetch valuation + peer comparison data — no LLM call."""
    if not ticker:
        return "[CƠ BẢN]\nKhông có mã cổ phiếu."

    # Cross-ticker comparison: two or more explicit tickers in the query (e.g. "HPG so với VCB")
    explicit_tickers = _extract_tickers_from_query(query)
    # Only treat as cross-ticker if ≥2 known tickers that differ from each other
    compare_tickers = [t for t in explicit_tickers if t != ticker] if ticker else []
    if _is_sector_comparison(query) and compare_tickers:
        # Build peer list: primary ticker + all explicitly mentioned tickers
        peers = list(dict.fromkeys([ticker] + compare_tickers))
        rows = [_fetch_valuation(t) for t in peers]
        return f"[SO SÁNH {' & '.join(peers)}]\n{_build_analysis(ticker, rows)}"

    if _is_sector_comparison(query):
        peers = get_sector_peers(ticker)
        rows = [_fetch_valuation(t) for t in peers]
        return f"[CƠ BẢN & ĐỊNH GIÁ {ticker}]\n{_build_analysis(ticker, rows)}"

    # Single ticker: just return key valuation metrics
    row = _fetch_valuation(ticker)
    lines = [f"[CƠ BẢN {ticker}]"]
    metric_labels = {
        "pe": "P/E", "pb": "P/B", "roe_pct": "ROE (%)", "roa_pct": "ROA (%)",
        "eps": "EPS", "gross_margin_pct": "Biên lợi nhuận gộp (%)",
        "net_margin_pct": "Biên lợi nhuận ròng (%)",
        "revenue_growth_pct": "Tăng trưởng doanh thu YoY (%)",
        "earnings_growth_pct": "Tăng trưởng LNST YoY (%)",
        "de_ratio": "D/E ratio", "ev_ebitda": "EV/EBITDA",
    }
    for k, label in metric_labels.items():
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            lines.append(f"  {label}: {v:.2f}" if isinstance(v, float) else f"  {label}: {v}")
    return "\n".join(lines)


@observe(name="intent.fundamentals")
def run(ticker: str | None, query: str) -> str:
    if ticker and _is_sector_comparison(query):
        peers    = get_sector_peers(ticker)
        rows     = [_fetch_valuation(t) for t in peers]
        analysis = _build_analysis(ticker, rows)
        data_table = _extract_data_table(analysis)

        user_prompt = f"""Câu hỏi: {query}

{analysis}

QUAN TRỌNG: Sử dụng CHÍNH XÁC các con số và nhận định đã tính sẵn ở trên.
KHÔNG tự suy luận lại thứ hạng hay so sánh — chỉ diễn giải kết quả.

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
TANG_TRUONG: [2-3 câu về tăng trưởng doanh thu và LNST YoY — dùng đúng % đã cho]
BIEN_LN: [1-2 câu về gross margin và net margin]
HIEU_QUA: [2-3 câu về ROE và ROA — dùng đúng xếp hạng đã tính]
SUC_KHOE: [1-2 câu về D/E ratio và CFO/LNST]
DINH_GIA: [2-3 câu về P/E, P/B, EV/EBITDA — premium/discount so ngành]
MOAT: [2-3 câu về lợi thế cạnh tranh suy luận từ margin + ROE + vị thế ngành]
NHAN_DINH: [1-2 câu nhận định tổng thể — dứt khoát]
</report>"""

        client = create_client()
        resp = client.generate(
            [Message(role="user", content=user_prompt)],
            max_tokens=1200,
            temperature=0,
            system=_SYSTEM,
        )

        from agents.intents import extract_report
        raw = extract_report(resp.text.strip())
        tang_truong = strip_thinking(extract_slot(raw, "TANG_TRUONG", "BIEN_LN"))
        bien_ln     = strip_thinking(extract_slot(raw, "BIEN_LN",     "HIEU_QUA"))
        hieu_qua    = strip_thinking(extract_slot(raw, "HIEU_QUA",    "SUC_KHOE"))
        suc_khoe    = strip_thinking(extract_slot(raw, "SUC_KHOE",    "DINH_GIA"))
        dinh_gia    = strip_thinking(extract_slot(raw, "DINH_GIA",    "MOAT"))
        moat        = strip_thinking(extract_slot(raw, "MOAT",        "NHAN_DINH"))
        nhan_dinh   = strip_thinking(extract_slot(raw, "NHAN_DINH",   None))

        if not tang_truong and not dinh_gia:
            return f"Không thể tra cứu tài chính **{ticker}** — vui lòng thử lại."

        return _assemble_fund_report(ticker, data_table, tang_truong, bien_ln, hieu_qua, suc_khoe, dinh_gia, moat, nhan_dinh)

    from rag.qa import answer as qa_answer
    return qa_answer(query, ticker=ticker)
