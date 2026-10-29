"""
data/insider_trades.py — insider / related-party / institutional-holder trade notices (VCI IQ events DDIND, DDINS,
DDRP via vnmarket) → insider_trades.

corporate_events_scraper skips these codes (they are not corporate actions); vn-portfolio's rule engine needs them
(khung v2.2 5.4, 9.1-B, 10.2-B, 10.7). The full source record is kept in `raw`, so new fields need no re-fetch.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

INSIDER_CODES = ("DDIND", "DDINS", "DDRP")
PAGE_LIMIT = 2000                    # VCI IQ page size; a full page means the window may be truncated
_DATE_KEYS = {"public_date": ("public_date", "display_date1", "notify_date"),
              "start_date": ("start_date", "display_date2", "exright_date"),
              "end_date": ("end_date", "display_date3", "record_date")}


def _plain(v):
    """pandas / numpy scalars → JSON-safe Python values (NaT / NaN → None)."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return None if v != v else v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        return _plain(v.item())
    return v


def _first_date(rec: dict, keys: tuple[str, ...]) -> date | None:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and len(v) >= 10:
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                continue
    return None


def fetch_insider_trades(start: date, end: date) -> list[dict]:
    """Every DDIND / DDINS / DDRP notice in [start, end] for the whole market, as insider_trades rows."""
    from core.vnmarket_client import get_client
    from vnmarket import NoData

    try:
        df = get_client().market.events(start, end, codes=",".join(INSIDER_CODES), limit=PAGE_LIMIT)
    except NoData:
        return []
    if len(df) >= PAGE_LIMIT:
        raise RuntimeError(f"insider_trades: {len(df)} rows for {start} → {end} hit the page limit; use a smaller window")
    rows = []
    for r in df.to_dict("records"):
        rec = {k: _plain(v) for k, v in r.items()}
        code = str(rec.get("event_code") or "").upper()
        if code not in INSIDER_CODES or not rec.get("ticker"):
            continue
        rows.append({"ticker": str(rec["ticker"]).upper(), "event_code": code,
                     "title": rec.get("event_title_vi") or rec.get("event_name_vi") or "",
                     **{k: _first_date(rec, keys) for k, keys in _DATE_KEYS.items()}, "raw": rec})
    return rows


def upsert_insider_trades(rows: list[dict]) -> int:
    """Insert new notices; a notice seen again refreshes its raw record (the source fills execution results later)."""
    if not rows:
        return 0
    from core.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO insider_trades (ticker, event_code, title, public_date, start_date, end_date, raw) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, event_code, title, public_date) "
            "DO UPDATE SET raw = EXCLUDED.raw, start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, "
            "fetched_at = NOW()",
            [(r["ticker"], r["event_code"], r["title"], r["public_date"], r["start_date"], r["end_date"],
              json.dumps(r["raw"], ensure_ascii=False, default=str)) for r in rows])
        conn.commit()
    return len(rows)


def ingest(days_back: int, chunk_days: int = 30) -> dict:
    """Fetch the last `days_back` days in `chunk_days` windows (one VCI page per window) and upsert."""
    end = date.today()
    start = end - timedelta(days=days_back)
    fetched = stored = 0
    while start <= end:
        stop = min(end, start + timedelta(days=chunk_days - 1))
        rows = fetch_insider_trades(start, stop)
        fetched += len(rows)
        stored += upsert_insider_trades(rows)
        start = stop + timedelta(days=1)
    return {"fetched": fetched, "stored": stored, "days_back": days_back}
