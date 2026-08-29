"""
agents/intents/fundamentals.py — Nhóm 3: Cơ bản & Định giá.

Fetches real valuation metrics (P/E, P/B, ROE, EPS) via yfinance,
pre-computes rankings in Python, injects factual statements into LLM prompt.
LLM only narrates — never re-derives comparisons from scratch.
Falls back to rag/qa.py for BCTC-specific questions.
"""

from __future__ import annotations

import math
from typing import Optional

import yfinance as yf

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking

_BANKING_PEERS  = ["VCB", "BID", "CTG", "MBB", "TCB", "VPB", "ACB", "STB"]
_STEEL_PEERS    = ["HPG", "HSG", "NKG", "TLH"]
_TECH_PEERS     = ["FPT", "CMG", "VGI"]
_REALESTATE     = ["VHM", "VIC", "NVL", "PDR", "DXG"]

_SECTOR_MAP: dict[str, list[str]] = {
    **{t: _BANKING_PEERS for t in _BANKING_PEERS},
    **{t: _STEEL_PEERS   for t in _STEEL_PEERS},
    **{t: _TECH_PEERS    for t in _TECH_PEERS},
    **{t: _REALESTATE    for t in _REALESTATE},
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_valuation(ticker: str) -> dict:
    try:
        info = yf.Ticker(f"{ticker}.VN").info
    except Exception:
        info = {}
    pe  = info.get("trailingPE") or info.get("forwardPE")
    roe = info.get("returnOnEquity")
    roa = info.get("returnOnAssets")
    gross_m = info.get("grossMargins")
    net_m   = info.get("profitMargins")
    rev_g   = info.get("revenueGrowth")
    earn_g  = info.get("earningsGrowth")
    de      = info.get("debtToEquity")
    op_cf   = info.get("operatingCashflow")
    fcf     = info.get("freeCashflow")
    net_inc = info.get("netIncomeToCommon")
    ev      = info.get("enterpriseValue")
    ebitda  = info.get("ebitda")
    return {
        "ticker":            ticker,
        "pe":                pe,
        "pb":                info.get("priceToBook"),
        "roe":               roe,
        "roe_pct":           roe * 100 if roe else None,
        "roa_pct":           roa * 100 if roa else None,
        "eps":               info.get("trailingEps"),
        "price":             info.get("currentPrice") or info.get("previousClose"),
        "gross_margin_pct":  gross_m * 100 if gross_m else None,
        "net_margin_pct":    net_m   * 100 if net_m   else None,
        "revenue_growth_pct": rev_g  * 100 if rev_g  else None,
        "earnings_growth_pct": earn_g * 100 if earn_g else None,
        "de_ratio":          de,
        "op_cashflow":       op_cf,
        "fcf":               fcf,
        "net_income":        net_inc,
        "ev_ebitda":         (ev / ebitda) if ev and ebitda and ebitda > 0 else None,
    }


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

def _is_sector_comparison(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in [
        "so với ngành", "so với các ngân hàng", "so với peer", "ngành ngân hàng",
        "toàn ngành", "so sánh", "trung bình ngành", "so với thị trường",
        "so với", "vs ngành", "p/e", "p/b", "roe", "eps",
    ])


def run(ticker: str | None, query: str) -> str:
    if ticker and _is_sector_comparison(query):
        peers = _SECTOR_MAP.get(ticker, [ticker])
        rows = [_fetch_valuation(t) for t in peers]
        analysis = _build_analysis(ticker, rows)

        prompt = f"""Câu hỏi: {query}

{analysis}

QUAN TRỌNG: Sử dụng CHÍNH XÁC các con số và nhận định đã tính sẵn ở trên.
KHÔNG tự suy luận lại thứ hạng hay so sánh — chỉ diễn giải kết quả.

Viết báo cáo Markdown (không văn bản trước báo cáo):
# Phân tích Cơ bản & Định giá {ticker}
## Tăng trưởng (Doanh thu & LNST YoY)
## Biên lợi nhuận (Gross Margin / Net Margin)
## Hiệu quả vốn (ROE, ROA — so sánh ngành)
## Sức khỏe tài chính (D/E, CFO vs LNST)
## Định giá (P/E, P/B, EV/EBITDA — so sánh ngành + premium/discount)
## Lợi thế cạnh tranh (Moat — suy luận từ biên lợi nhuận + ROE + vị thế)
## Nhận định tổng thể
[Nguồn: yfinance]"""

        client = create_client()
        resp = client.generate(
            [Message(role="user", content=prompt)],
            max_tokens=1500,
            temperature=0,
            system=(
                "Bạn là chuyên gia phân tích định giá chứng khoán Việt Nam. "
                "Xuất NGAY báo cáo Markdown. "
                "TUYỆT ĐỐI KHÔNG tự tính lại thứ hạng — dùng nguyên kết quả đã cho. "
                "TUYỆT ĐỐI KHÔNG thêm 'có thể', 'có lẽ', 'dường như' khi so sánh số liệu từ bảng — "
                "số đã cho là sự thật, viết dứt khoát. "
                "TUYỆT ĐỐI KHÔNG viết suy nghĩ hay meta-commentary. Chỉ báo cáo cuối cùng."
            ),
        )
        return strip_thinking(strip_preamble(resp.text.strip()))

    from rag.qa import answer as qa_answer
    return qa_answer(query, ticker=ticker)
