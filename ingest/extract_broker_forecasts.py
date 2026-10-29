"""
ingest/extract_broker_forecasts.py — EPS / NPAT forecasts from broker research PDFs → broker_forecasts.

broker_reports (fetch_rule_data.py broker_reports, Simplize list) gives broker, date, target price and the PDF. The
forecast table is on the first pages of every report but the PDF text comes out as fragments, so an LLM reads the
first pages and returns forecasts per fiscal year with the line it read them from. A figure is kept only when its
quote is found in the PDF text (whitespace-insensitive) and the quote carries the figure's leading digits — numbers
the model made up or converted wrongly are dropped. vn-portfolio averages ≥ 2 brokers within 90 days (khung 6.5-B).

Usage:
    python ingest/extract_broker_forecasts.py                    # unread reports issued in the last 400 days
    python ingest/extract_broker_forecasts.py --since 2024-01-01 --tickers HPG,FPT
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.db import get_conn

PDF_HOSTS = ("https://cdn.simplize.vn/",)
MAX_PAGES = 3
MAX_CHARS = 14000
DEFAULT_LOOKBACK_DAYS = 400
EPS_RANGE = (50.0, 200_000.0)                # VND per share
NPAT_MAX = 200_000e9                          # VND; larger is a unit error

_TOOL = {
    "name": "save_forecasts",
    "description": "Save the broker's own forecasts for future fiscal years from a Vietnamese equity research report",
    "parameters": {
        "type": "object",
        "properties": {
            "forecasts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fiscal_year": {"type": "integer"},
                        "eps_vnd": {"type": ["number", "null"], "description": "Forecast EPS, VND per share"},
                        "npat_parent_vnd": {"type": ["number", "null"],
                                            "description": "Forecast net profit after tax attributable to the parent, full VND"},
                        "revenue_vnd": {"type": ["number", "null"], "description": "Forecast net revenue, full VND"},
                        "quote": {"type": "string", "description": "The row/sentence the numbers were read from, copied exactly"},
                    },
                    "required": ["fiscal_year", "quote"],
                },
            }
        },
        "required": ["forecasts"],
    },
}


def _reports(tickers: list[str] | None, since: date, force: bool) -> list[dict]:
    sql = ("SELECT id, ticker, broker, issue_date, title, pdf_url FROM broker_reports WHERE issue_date >= %s "
           "AND pdf_url IS NOT NULL" + ("" if force else " AND read_at IS NULL") +
           (" AND ticker = ANY(%s)" if tickers else "") + " ORDER BY issue_date DESC")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (since, tickers) if tickers else (since,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _layout(page) -> str:
    """Page text rebuilt from word positions: one line per visual row, ' | ' where words are far apart, so a
    forecast row stays aligned with its year header ('Năm | 2025 | 2026F | 2027F' / 'LNTT | 37,788 | 42,735 | 48,602')."""
    words = sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows: list[list] = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - yc) <= 2.5:
            rows[-1][1].append(w)
        else:
            rows.append([yc, [w]])
    lines = []
    for _, ws in rows:
        ws.sort(key=lambda w: w[0])
        out, prev = "", None
        for w in ws:
            out += ("" if prev is None else " | " if w[0] - prev > 8 else " ") + w[4]
            prev = w[2]
        lines.append(out)
    return "\n".join(lines)


def _pdf_text(url: str) -> str:
    import httpx
    import pymupdf

    if not url.startswith(PDF_HOSTS):
        raise ValueError(f"unexpected PDF host: {url[:60]}")
    r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    doc = pymupdf.open(stream=r.content, filetype="pdf")
    return "\n".join(_layout(doc[i]) for i in range(min(MAX_PAGES, len(doc))))


def _squash(s: str) -> str:
    return re.sub(r"[\s|]+", "", s.lower())


def _ask(client, rep: dict, text: str) -> list[dict]:
    from llm.types import Message

    y = rep["issue_date"].year
    prompt = (
        f"Đây là trang đầu báo cáo phân tích cổ phiếu {rep['ticker']} của {rep['broker']} ngày {rep['issue_date']} "
        f"(văn bản trích từ PDF theo bố cục: mỗi dòng là một hàng, ' | ' ngăn cột; số của một hàng bảng thẳng cột với dòng tiêu đề năm phía trên). Trả về DỰ PHÓNG của chính công ty chứng khoán cho các năm tài "
        f"chính {y} đến {y + 2} (cột có chữ F/E/dự phóng/forecast, hoặc câu 'dự phóng LNST năm …'): EPS (đồng/cổ "
        f"phiếu), LNST của cổ đông công ty mẹ, doanh thu thuần. Không lấy số thực hiện của năm đã qua, không lấy số "
        f"quý. Đổi về đồng theo đơn vị báo cáo ghi (tỷ đồng ×1.000.000.000, triệu ×1.000.000; 'bn VND' = tỷ). "
        f"Năm nào không có dự phóng thì bỏ qua. 'quote' chép nguyên văn dòng/câu chứa số đã đọc.\n\n{text[:MAX_CHARS]}"
    )
    resp = client.generate(messages=[Message(role="user", content=prompt)], tools=[_TOOL], max_tokens=1500,
                           temperature=0)
    raw = resp.tool_calls[0].input.get("forecasts") if resp.tool_calls else None
    if raw is None:
        m = re.search(r"\{.*\}|\[.*\]", resp.text or "", re.S)
        try:
            j = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            j = {}
        raw = j.get("forecasts", []) if isinstance(j, dict) else j
    return raw or []


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _year(cell: str) -> int | None:
    """Forecast/actual column header → year: '2026F', '2026E', 'FY26F', '26F', '12/26F', '2026F (Mới)', '2025'.
    A bare two-digit number is not a year; an old-forecast column '(Cũ)' is not used."""
    c = cell.strip()
    if re.search(r"cũ|old|prev", c, re.I):
        return None
    c = re.sub(r"\s*\((mới|new)\)\s*$", "", c, flags=re.I)
    m = re.fullmatch(r"(?:FY\s*)?(20\d{2})\s*\(?[FEfeA]?\)?\*?|(?:FY\s*|\d{1,2}/)?'?(\d{2})\s*\(?[FEfe]\)?\*?|"
                     r"\d{1,2}/(20\d{2}|\d{2})\s*\(?[FEfeA]?\)?", c)
    if not m:
        return None
    y = next(g for g in m.groups() if g)
    return int(y) if len(y) == 4 else 2000 + int(y)


LABELS = {"eps": r"\bEPS\b", "npat_parent": r"LNST|lợi nhuận (ròng|sau thuế)|NPAT|net (profit|income)",
          "revenue": r"doanh thu( thuần)?\b|net (revenue|sales)|\brevenue\b"}
NOT_LEVEL = r"tăng trưởng|growth|%|biên|margin|yoy|svck|kỳ vọng|consensus|thị trường"


def _cell_num(c: str) -> str:
    return re.sub(r"\D", "", c) if re.fullmatch(r"\(?-?[\d.,]+\)?", c.strip()) else ""


def placed(text: str, metric: str, value: float, fy: int) -> str | None:
    """The row of the document that puts `value` under fiscal year `fy` for `metric`, or None. A table row: its label
    cell names the metric (not a growth / margin / consensus row) and the value's cell, counted from the right, lines
    up with `fy` in the nearest year-header row above. Prose: one sentence naming the metric, the year and the value.
    Guards against the model reading the right row but the wrong column, or making a number up."""
    lead = re.sub(r"0+$", "", str(int(round(abs(value)))))[:3]
    if not lead:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.split(" | ")]
        if len(cells) == 1:
            if (re.search(LABELS[metric], line, re.I) and re.search(rf"\b{fy}\b|\b{fy % 100}[FE]\b", line)
                    and re.search(r"dự phóng|ước|kế hoạch|forecast|expect", line, re.I) and lead in re.sub(r"\D", "", line)):
                return line
            continue
        hits = []
        for j, c in enumerate(cells):
            if _cell_num(c).startswith(lead):
                label = next((x for x in reversed(cells[:j]) if not _cell_num(x)), "")
                if re.search(LABELS[metric], label, re.I) and not re.search(NOT_LEVEL, label, re.I):
                    hits.append(j)
        if not hits:
            continue
        for back in range(1, 41):
            if i - back < 0:
                break
            years = [_year(c) for c in lines[i - back].split(" | ")]
            if sum(y is not None for y in years) < 2:
                continue
            for j in hits:
                k = len(cells) - 1 - j
                if k < len(years) and years[len(years) - 1 - k] == fy:
                    return lines[i - back] + "\n" + line
            break                                              # the nearest header decides
    return None


def check(rep: dict, raw: list[dict], text: str) -> list[dict]:
    """The model proposes (year, EPS, NPAT, revenue); each figure is kept only when `placed` finds the document row
    that carries it under that year — that row becomes the stored quote. Plausible ranges; forecast years only."""
    y0 = rep["issue_date"].year
    by_year: dict[int, dict] = {}
    for f in raw:
        try:
            fy = int(f.get("fiscal_year"))
        except (TypeError, ValueError):
            continue
        if not (y0 <= fy <= y0 + 2):
            continue
        node = by_year.setdefault(fy, {"fiscal_year": fy, "eps": None, "npat_parent": None, "revenue": None,
                                       "quote": []})
        vals = {"eps": _num(f.get("eps_vnd")), "npat_parent": _num(f.get("npat_parent_vnd")),
                "revenue": _num(f.get("revenue_vnd"))}
        for metric, v in vals.items():
            if v is None or node[metric] is not None:
                continue
            if metric == "eps" and not EPS_RANGE[0] <= v <= EPS_RANGE[1]:
                continue
            if metric != "eps" and not 0 < abs(v) <= NPAT_MAX * (20 if metric == "revenue" else 1):
                continue
            row = placed(text, metric, v, fy)
            if row:
                node[metric] = v
                node["quote"].append(row)
    out = []
    for node in by_year.values():
        if node["eps"] is not None or node["npat_parent"] is not None:
            node["quote"] = "\n".join(dict.fromkeys(node["quote"]))[:600]
            out.append(node)
    return out


def _store(rep: dict, facts: list[dict], status: str, model: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        for f in facts:
            cur.execute("""INSERT INTO broker_forecasts (report_id, fiscal_year, eps, npat_parent, revenue, quote, model)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (report_id, fiscal_year) DO UPDATE SET
                           eps=EXCLUDED.eps, npat_parent=EXCLUDED.npat_parent, revenue=EXCLUDED.revenue,
                           quote=EXCLUDED.quote, model=EXCLUDED.model, extracted_at=NOW()""",
                        (rep["id"], f["fiscal_year"], f["eps"], f["npat_parent"], f["revenue"], f["quote"], model))
        cur.execute("UPDATE broker_reports SET read_at = NOW(), read_status = %s WHERE id = %s", (status, rep["id"]))
    return len(facts)


def _one(client, model: str, rep: dict) -> tuple[str, int, str | None]:
    """Read one report → (status, forecasts stored, error). An LLM failure leaves it unread for the next run."""
    try:
        text = _pdf_text(rep["pdf_url"])
    except Exception as exc:
        _store(rep, [], "pdf_error", model)
        return "pdf_error", 0, repr(exc)[:120]
    if len(text.strip()) < 200:                       # scanned PDF without a text layer
        _store(rep, [], "no_text", model)
        return "no_text", 0, None
    try:
        facts = check(rep, _ask(client, rep, text), text)
    except Exception as exc:
        return "llm_error", 0, repr(exc)[:120]
    status = "ok" if facts else "no_forecast"
    return status, _store(rep, facts, status, model), None


def run(tickers: list[str] | None = None, since: date | None = None, force: bool = False,
        limit: int | None = None, workers: int = 6) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from llm import create_client

    since = since or date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    reps = _reports(tickers, since, force)
    reps = reps[:limit] if limit else reps
    client = create_client()
    model = getattr(client, "model", None) or type(client).__name__
    status_count: dict[str, int] = {}
    stored, failed = 0, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rep, (status, n, err) in zip(reps, pool.map(lambda r: _one(client, model, r), reps)):
            status_count[status] = status_count.get(status, 0) + 1
            stored += n
            if err:
                failed.append((rep["id"], err))
    read = sum(v for k, v in status_count.items() if k != "llm_error")
    if reps and status_count.get("llm_error", 0) == len(reps):
        raise RuntimeError(f"broker_forecasts: the LLM answered no report; failed={failed[:5]}")
    return {"reports": len(reps), "read": read, "forecasts": stored, "status": status_count,
            "failed": len(failed), "failed_sample": failed[:5]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Broker research PDFs → broker_forecasts")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] or None
    print(run(tickers, a.since, a.force, a.limit, a.workers))


if __name__ == "__main__":
    main()
