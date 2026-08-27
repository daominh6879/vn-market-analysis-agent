"""
data/corporate_events_scraper.py — Scrape corporate event calendar from CafeF.

Source: https://cafef.vn/lich-su-kien.chn (HTML table, public, no login).
Returns list of dicts ready for upsert into corporate_events table.

Usage:
    python data/corporate_events_scraper.py               # print today ± 7 days
    python data/corporate_events_scraper.py --dry-run
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

_CAFEF_URL = "https://cafef.vn/du-lieu/lich-su-kien.chn"

# Map Vietnamese event description keywords → normalized event_type
_EVENT_TYPE_MAP = [
    ("giao dịch không hưởng quyền", "gdkhq"),
    ("gdkhq",                        "gdkhq"),
    ("chốt danh sách",               "record_date"),
    ("phát hành cổ phiếu",           "rights_issue"),
    ("tăng vốn",                     "rights_issue"),
    ("cổ tức",                       "dividend"),
    ("lợi tức",                      "dividend"),
    ("đại hội",                      "agm"),
    ("đhcđ",                         "agm"),
]

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_RATIO_RE = re.compile(r"(\d+)[:\s]*(\d+)")


def _classify_event(raw_text: str) -> str:
    lower = raw_text.lower()
    for keyword, etype in _EVENT_TYPE_MAP:
        if keyword in lower:
            return etype
    return "other"


def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _extract_ratio(note: str) -> Optional[float]:
    """Extract ratio from text like '100:10' → 10.0, '5%' → 5.0."""
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", note)
    if pct:
        return float(pct.group(1))
    m = _RATIO_RE.search(note)
    if m:
        base, part = int(m.group(1)), int(m.group(2))
        if base > 0:
            return round(part / base * 100, 2)
    return None


_TCBS_EVENTS_URL = "https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/events"


def scrape_tcbs_events(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    tickers: Optional[list[str]] = None,
) -> list[dict]:
    """
    Fetch corporate events from TCBS public API (no auth required).

    Queries per-ticker for VN30 universe (or provided list).
    Returns list of dicts matching corporate_events schema.

    TCBS endpoint: GET /stock-insight/v1/stock/events?ticker=HPG&page=0&size=10
    Response JSON: {"data": [{"ticker", "eventName", "exrightDate",
                               "recordDate", "issueDate", "ratio"}, ...]}
    """
    try:
        import httpx
    except ImportError as e:
        sys.stderr.write(f"[tcbs_events] Missing dependency: {e}\n")
        return []

    if start_date is None:
        start_date = date.today() - timedelta(days=1)
    if end_date is None:
        end_date = date.today() + timedelta(days=14)
    if tickers is None:
        from data.hose_universe import get_vn30_tickers
        tickers = get_vn30_tickers()

    events: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    for ticker in tickers:
        try:
            resp = httpx.get(
                _TCBS_EVENTS_URL,
                params={"ticker": ticker, "page": "0", "size": "10"},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            sys.stderr.write(f"[tcbs_events] {ticker}: {e}\n")
            continue

        items = payload if isinstance(payload, list) else payload.get("data", [])
        for item in items:
            # TCBS date fields: "exrightDate" / "recordDate" / "issueDate" (YYYY-MM-DD or DD/MM/YYYY)
            raw_exdate = item.get("exrightDate") or item.get("exDate") or ""
            raw_recdate = item.get("recordDate") or ""
            note = item.get("eventName") or item.get("name") or ""

            ex_date = _parse_date(raw_exdate) if raw_exdate else None
            record_date = _parse_date(raw_recdate) if raw_recdate else None
            effective_date = ex_date or record_date
            if effective_date is None or not (start_date <= effective_date <= end_date):
                continue

            etype = _classify_event(note)
            ratio_raw = item.get("ratio") or item.get("value") or ""
            ratio = _extract_ratio(str(ratio_raw)) if ratio_raw else None

            events.append({
                "ticker": ticker.upper(),
                "event_type": etype,
                "ex_date": ex_date or effective_date,
                "record_date": record_date,
                "ratio": ratio,
                "note": note,
                "source_url": _TCBS_EVENTS_URL,
            })

    return events


def scrape_events(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Scrape corporate events: CafeF first (aggregate view), TCBS as fallback.

    Returns combined deduplicated list.
    """
    events = scrape_cafef_events(start_date, end_date)
    if not events:
        sys.stderr.write("[corporate_events_scraper] CafeF returned 0 events — trying TCBS\n")
        events = scrape_tcbs_events(start_date, end_date)
    return events


def scrape_cafef_events(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Scrape corporate events from CafeF calendar page.

    CafeF uses ASP.NET WebForms: GET returns unfiltered data (all years).
    Must POST with viewstate + date params to get events for a specific range.

    Returns list of dicts matching corporate_events schema:
        ticker, event_type, ex_date, record_date, ratio, note, source_url
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError as e:
        sys.stderr.write(f"[corporate_events_scraper] Missing dependency: {e}\n")
        return []

    if start_date is None:
        start_date = date.today() - timedelta(days=1)
    if end_date is None:
        end_date = date.today() + timedelta(days=14)

    _headers = {"User-Agent": "Mozilla/5.0"}
    events: list[dict] = []

    try:
        # Step 1: GET to obtain current viewstate tokens
        get_resp = httpx.get(_CAFEF_URL, timeout=15, headers=_headers)
        get_resp.raise_for_status()
        get_soup = BeautifulSoup(get_resp.text, "html.parser")
        viewstate = get_soup.find("input", {"name": "__VIEWSTATE"})
        vsg = get_soup.find("input", {"name": "__VIEWSTATEGENERATOR"})

        # Step 2: POST with date range — CafeF date format DD/MM/YYYY
        post_data = {
            "__VIEWSTATE": viewstate["value"] if viewstate else "",
            "__VIEWSTATEGENERATOR": vsg["value"] if vsg else "",
            "ctl00$ContentPlaceHolder1$LichSuKien2$txtKeyword": "",
            "ctl00$ContentPlaceHolder1$LichSuKien2$dpkTradeDate1$txtDatePicker": start_date.strftime("%d/%m/%Y"),
            "ctl00$ContentPlaceHolder1$LichSuKien2$dpkTradeDate2$txtDatePicker": end_date.strftime("%d/%m/%Y"),
            "ctl00$ContentPlaceHolder1$LichSuKien2$btSearch.x": "0",
            "ctl00$ContentPlaceHolder1$LichSuKien2$btSearch.y": "0",
            "ctl00$ContentPlaceHolder1$LichSuKien2$hdfStatus": "1",
            "ctl00$ContentPlaceHolder1$hdfPageIndex": "",
            "ctl00$ContentPlaceHolder1$hdfSymbol": "",
        }
        resp = httpx.post(
            _CAFEF_URL,
            data=post_data,
            timeout=15,
            headers={**_headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
    except Exception as e:
        sys.stderr.write(f"[corporate_events_scraper] HTTP error: {e}\n")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # CafeF renders a table with columns: Mã CK | Tên công ty | Ngày GDKHQ | Nội dung
    table = soup.find("table", {"id": re.compile(r"(?i)lich|event|dk")})
    if table is None:
        # Fallback: find first table with ≥4 columns and header mentions mã/ticker
        for t in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
            if any("mã" in h or "ticker" in h for h in headers):
                table = t
                break

    if table is None:
        sys.stderr.write("[corporate_events_scraper] Could not find events table in CafeF HTML.\n")
        return []

    # Detect column layout from header row
    header_row = table.find("tr")
    col_headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])] if header_row else []

    # New CafeF layout (2026+): ex_date | record_date | impl_date | ticker | exchange | note | price
    # Old layout: ticker | ... | date | ... | note
    # Detect by checking if col[3] header contains "mã"
    new_layout = len(col_headers) >= 4 and ("mã" in col_headers[3] or "ck" in col_headers[3])

    _NULL_DATE = date(1, 1, 1)  # "01/01/0001" placeholder used by CafeF for N/A dates

    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if new_layout:
            if len(cells) < 6:
                continue
            ticker = cells[3].upper().strip()
            if not re.match(r"^[A-Z]{2,5}$", ticker):
                continue
            ex_date = _parse_date(cells[0])
            record_date = _parse_date(cells[1])
            impl_date = _parse_date(cells[2])
            # Treat CafeF null sentinel as None
            if ex_date == _NULL_DATE:
                ex_date = None
            if record_date == _NULL_DATE:
                record_date = None
            # Use impl_date as ex_date fallback
            effective_date = ex_date or record_date or impl_date
            note = cells[5] if len(cells) > 5 else ""
        else:
            if len(cells) < 3:
                continue
            ticker = cells[0].upper().strip()
            if not re.match(r"^[A-Z]{2,5}$", ticker):
                continue
            ex_date = None
            record_date = None
            effective_date = None
            for cell in cells[2:]:
                d = _parse_date(cell)
                if d and d != _NULL_DATE:
                    effective_date = d
                    ex_date = d
                    break
            note = cells[-1] if len(cells) >= 4 else ""

        if effective_date is None or not (start_date <= effective_date <= end_date):
            continue

        etype = _classify_event(note)
        ratio = _extract_ratio(note)

        events.append({
            "ticker": ticker,
            "event_type": etype,
            "ex_date": ex_date or effective_date,
            "record_date": record_date,
            "ratio": ratio,
            "note": note,
            "source_url": _CAFEF_URL,
        })

    return events


def cleanup_old_events(days_keep: int = 30) -> int:
    """Delete events whose ex_date is older than `days_keep` days ago. Returns deleted count."""
    from data.db import get_conn
    cutoff = date.today() - timedelta(days=days_keep)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM corporate_events WHERE ex_date < %s", [cutoff])
            deleted = cur.rowcount
        conn.commit()
    return deleted


def upsert_events(events: list[dict]) -> int:
    """Upsert events into corporate_events table. Returns count inserted/updated."""
    if not events:
        return 0
    from data.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO corporate_events
                    (ticker, event_type, ex_date, record_date, ratio, note, source_url)
                VALUES (%(ticker)s, %(event_type)s, %(ex_date)s, %(record_date)s,
                        %(ratio)s, %(note)s, %(source_url)s)
                ON CONFLICT (ticker, event_type, ex_date)
                DO UPDATE SET
                    record_date = EXCLUDED.record_date,
                    ratio       = EXCLUDED.ratio,
                    note        = EXCLUDED.note,
                    fetched_at  = NOW()
                """,
                events,
            )
    return len(events)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Scrape CafeF corporate events")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days-back",  type=int, default=1)
    parser.add_argument("--days-ahead", type=int, default=14)
    args = parser.parse_args()

    start = date.today() - timedelta(days=args.days_back)
    end   = date.today() + timedelta(days=args.days_ahead)
    events = scrape_cafef_events(start, end)
    print(f"Scraped {len(events)} events from CafeF ({start} → {end})")
    for e in events:
        print(f"  {e['ticker']:6s} | {e['event_type']:12s} | {e['ex_date']} | {e['note'][:60]}")
    if args.dry_run or not events:
        return
    n = upsert_events(events)
    print(f"Upserted {n} rows into corporate_events")


if __name__ == "__main__":
    main()
