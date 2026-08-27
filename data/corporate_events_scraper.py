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

_CAFEF_URL = "https://cafef.vn/lich-su-kien.chn"

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


def scrape_cafef_events(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Scrape corporate events from CafeF calendar page.

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

    events: list[dict] = []
    try:
        resp = httpx.get(
            _CAFEF_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
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

    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 3:
            continue

        ticker = cells[0].upper().strip()
        if not re.match(r"^[A-Z]{2,5}$", ticker):
            continue

        # Find date column — scan all cells for DD/MM/YYYY
        ex_date: Optional[date] = None
        for cell in cells[2:]:
            d = _parse_date(cell)
            if d:
                ex_date = d
                break

        if ex_date and not (start_date <= ex_date <= end_date):
            continue

        note = cells[-1] if len(cells) >= 4 else ""
        etype = _classify_event(note)
        ratio = _extract_ratio(note)

        events.append({
            "ticker": ticker,
            "event_type": etype,
            "ex_date": ex_date,
            "record_date": None,
            "ratio": ratio,
            "note": note,
            "source_url": _CAFEF_URL,
        })

    return events


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
