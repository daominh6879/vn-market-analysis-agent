"""
data/cafef_insider.py — insider / related-person / major-holder deals with volumes from CafeF's market-data service
(GDCoDong, the same DataHistory service vnmarket reads foreign and proprietary flows from) → insider_deals.

Each row: who (name, position, related insider), holding before / after, registered buy / sell with the plan window,
executed buy / sell with the completion date, publication time. Read by vn-portfolio's rule engine (khung v2.2 5.4,
10.7) through /advisor/news-facts. Pages are newest first; a ticker is walked back until a page is older than `since`.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

import httpx

URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDCoDong.ashx"
HEADERS = {"User-Agent": "vn-market-analysis-agent/insider-deals (+data pipeline)"}
TIMEOUT = 20
PAGE_SIZE = 50
PAUSE_S = 0.4                    # between requests: one ticker at a time, politely
VN = timezone(timedelta(hours=7))
_MS = re.compile(r"/Date\((-?\d+)\)/")


def _ts(v) -> datetime | None:
    m = _MS.search(v or "") if isinstance(v, str) else None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc) if m else None


def _day(v) -> date | None:
    t = _ts(v)
    return t.astimezone(VN).date() if t else None


def _int(v) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def parse_row(r: dict) -> dict:
    """One CafeF record → insider_deals columns. Pure."""
    return {
        "ticker": str(r.get("Stock") or "").upper(), "holder_code": r.get("ShareHolderCode") or "",
        "person": (r.get("TransactionMan") or "").strip(), "position": (r.get("TransactionManPosition") or "").strip(),
        "related_person": (r.get("RelatedMan") or "").strip(),
        "related_position": (r.get("RelatedManPosition") or "").strip(),
        "volume_before": _int(r.get("VolumeBeforeTransaction")), "volume_after": _int(r.get("VolumeAfterTransaction")),
        "ownership_pct": r.get("TyLeSoHuu"), "plan_buy": _int(r.get("PlanBuyVolume")),
        "plan_sell": _int(r.get("PlanSellVolume")), "plan_begin": _day(r.get("PlanBeginDate")),
        "plan_end": _day(r.get("PlanEndDate")), "real_buy": _int(r.get("RealBuyVolume")),
        "real_sell": _int(r.get("RealSellVolume")), "real_end": _day(r.get("RealEndDate")),
        "published_at": _ts(r.get("PublishedDate")), "note": (r.get("TransactionNote") or "").strip(),
    }


def fetch_ticker(ticker: str, since: date, client: httpx.Client | None = None, max_pages: int = 40) -> list[dict]:
    """Every deal of `ticker` published on/after `since` (newest-first pages until one ends before `since`)."""
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=TIMEOUT)
    out: list[dict] = []
    try:
        for page in range(1, max_pages + 1):
            r = client.get(URL, params={"Symbol": ticker, "PageIndex": page, "PageSize": PAGE_SIZE})
            r.raise_for_status()
            rows = ((r.json() or {}).get("Data") or {}).get("Data") or []
            if not rows:
                break
            parsed = [parse_row(x) for x in rows]
            out += [p for p in parsed if p["published_at"] is None or p["published_at"].astimezone(VN).date() >= since]
            oldest = min((p["published_at"] for p in parsed if p["published_at"]), default=None)
            if len(rows) < PAGE_SIZE or (oldest and oldest.astimezone(VN).date() < since):
                break
            time.sleep(PAUSE_S)
    finally:
        if own:
            client.close()
    return out


def upsert_deals(rows: list[dict]) -> int:
    """Insert new deals; a deal seen again (CafeF fills executed volumes later) is updated, first_seen_at kept."""
    if not rows:
        return 0
    from core.db import get_conn
    cols = ["ticker", "holder_code", "person", "position", "related_person", "related_position", "volume_before",
            "volume_after", "ownership_pct", "plan_buy", "plan_sell", "plan_begin", "plan_end", "real_buy",
            "real_sell", "real_end", "published_at", "note"]
    upd = [c for c in cols if c not in ("ticker", "holder_code", "person", "plan_begin", "plan_end")]
    sql = (f"INSERT INTO insider_deals ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
           "ON CONFLICT (ticker, holder_code, person, plan_begin, plan_end) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in upd) + ", updated_at = NOW()")
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        conn.commit()
    return len(rows)


def ingest(tickers: list[str], days_back: int) -> dict:
    """All tickers, deals published in the last `days_back` days. A failing ticker is logged and skipped."""
    since = date.today() - timedelta(days=days_back)
    stored, failed = 0, []
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT) as client:
        for t in tickers:
            try:
                rows = [r for r in fetch_ticker(t, since, client) if r["ticker"]]
                stored += upsert_deals(rows)
            except (httpx.HTTPError, ValueError) as exc:
                failed.append(f"{t}: {type(exc).__name__}")
            time.sleep(PAUSE_S)
    return {"tickers": len(tickers), "stored": stored, "failed": failed[:20], "failed_count": len(failed),
            "days_back": days_back}
