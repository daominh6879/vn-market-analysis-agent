"""Facts that only exist in the news, read from CafeF articles listed in its public sitemaps (robots.txt allows them):

  interbank      SBV's interbank rates (overnight, 1 week, 2 weeks, 1 month, 3 months) as reported, with the date
                 they refer to → macro_indicators interbank_<tenor> (source cafef), published_at = article time.
                 SBV's own site blocks automated access; the articles re-publish its figures.
  index_review   HOSE index reviews (VN30, VN100, VNDiamond, VNFinLead, VNFinSelect, VNMID, VNSML): tickers added /
                 removed and the effective date → index_membership_events, announced_at = article time.

Each article is fetched once into web_articles; an LLM reads it and returns facts with the sentence quoted. A fact is
kept only when its number (or ticker) is in the quote, the quote is in the article, and its date fits the article's
(observation ≤ article date and at most 14 days earlier; effective date within 60 days after). Backfill walks the
sitemaps from 2019; the daily run reads the last 10 days.

    python -m ingest.web_facts --since 2019-01-01            # backfill
    python -m ingest.web_facts                               # last 10 days
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.db import get_conn  # noqa: E402

SITEMAP_INDEX = "https://cafef.vn/sitemap.xml"
FETCH_PAUSE = 1.0                                   # seconds between requests to cafef.vn
TZ = "Asia/Ho_Chi_Minh"
TOPICS = {
    "interbank": re.compile(r"lien-ngan-hang|qua-dem"),
    "index_review": re.compile(r"(?=.*(vn30|vn100|vndiamond|vnfin|vnmid|vnsml|vnx50|ro-chi-so|chi-so-vn))"
                               r"(?=.*(co-cau|review|loai|them|bo-sung|thay-doi|ra-khoi|vao-ro))"),
}
TENORS = {"overnight": "on", "1w": "1w", "2w": "2w", "1m": "1m", "3m": "3m"}
INDICES = {"VN30", "VN100", "VNDIAMOND", "VNFINLEAD", "VNFINSELECT", "VNMID", "VNSML", "VNX50"}
RATE_RANGE = (0.05, 25.0)
MAX_CHARS = 9000


# ── crawl ────────────────────────────────────────────────────────────────────

def _get(url: str) -> str:
    import httpx
    time.sleep(FETCH_PAUSE)
    r = httpx.get(url, timeout=40, follow_redirects=True)
    r.raise_for_status()
    return r.text


def sitemap_files(since: date, until: date | None = None) -> list[str]:
    """cafef.vn/sitemaps/sitemaps-YYYY-M-D1-D2.xml (5–6 days each) overlapping [since, until]."""
    out = []
    for u in re.findall(r"<loc>([^<]+)</loc>", _get(SITEMAP_INDEX)):
        m = re.search(r"sitemaps-(\d{4})-(\d{1,2})-\d{1,2}-(\d{1,2})\.xml$", u)
        if m:
            last = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if last >= since and (until is None or last - timedelta(days=6) <= until):
                out.append(u)
    return out


def article(url: str) -> dict:
    t = _get(url)
    pub = re.search(r'<meta property="article:published_time"\s+content="([^"]+)"', t)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S)
    i = t.find('data-role="content"')
    seg = t[i:i + 40000] if i >= 0 else ""
    ends = [x for x in (seg.find("link-source-detail"), seg.find("tagdetail"), seg.find("VCSortableInPreviewMode")) if x > 0]
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", seg[:min(ends) if ends else len(seg)], flags=re.S)
    import pandas as pd
    published = pd.Timestamp(pub.group(1)).tz_localize(TZ) if pub and "+" not in pub.group(1) else (
        pd.Timestamp(pub.group(1)) if pub else None)
    return {"published_at": published.to_pydatetime() if published is not None else None,
            "title": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title.group(1)))).strip() if title else None,
            "body": re.sub(r"\s+", " ", html.unescape(body)).replace('data-role="content">', "").strip()}


def crawl(since: date, topics: tuple[str, ...], until: date | None = None) -> dict:
    """New article URLs of the topics → web_articles (fetched once)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT url FROM web_articles")
        have = {r[0] for r in cur.fetchall()}
    files, found, fetched, errors = sitemap_files(since, until), 0, 0, []
    for sm in files:
        try:
            urls = re.findall(r"<loc>([^<]+)</loc>", _get(sm))
        except Exception as e:                                  # one sitemap failing must not stop the walk
            errors.append((sm, repr(e)[:80]))
            continue
        for u in urls:
            slug = u.rsplit("/", 1)[-1]
            topic = next((k for k in topics if TOPICS[k].search(slug)), None)
            if not topic or u in have:
                continue
            found += 1
            try:
                a = article(u)
                status = None if a["body"] else "fetch_error"
            except Exception as e:
                a, status = {"published_at": None, "title": None, "body": None}, "fetch_error"
                errors.append((u, repr(e)[:80]))
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("""INSERT INTO web_articles (url, site, topic, published_at, title, body, read_status, read_at)
                               VALUES (%s, 'cafef', %s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END)
                               ON CONFLICT (url) DO NOTHING""",
                            (u, topic, a["published_at"], a["title"], a["body"], status, status))
            have.add(u)
            fetched += 1
    return {"sitemaps": len(files), "articles_found": found, "fetched": fetched, "errors": len(errors),
            "error_sample": errors[:5]}


# ── read ─────────────────────────────────────────────────────────────────────

_TOOLS = {
    "interbank": {
        "name": "save_rates",
        "description": "Save the Vietnamese VND interbank interest rates the article reports",
        "parameters": {"type": "object", "required": ["rates"], "properties": {"rates": {"type": "array", "items": {
            "type": "object", "required": ["date", "tenor", "rate_pct", "quote"],
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD the rate refers to (the trading day, or the last day of the week for a weekly figure)"},
                "tenor": {"type": "string", "enum": list(TENORS)},
                "rate_pct": {"type": "number", "description": "Rate in percent per year, one exact figure"},
                "quote": {"type": "string", "description": "The sentence the rate was read from, copied exactly"},
            }}}}},
    },
    "index_review": {
        "name": "save_changes",
        "description": "Save the official HOSE index review changes the article reports",
        "parameters": {"type": "object", "required": ["changes"], "properties": {"changes": {"type": "array", "items": {
            "type": "object", "required": ["index", "ticker", "action", "effective_date", "quote"],
            "properties": {
                "index": {"type": "string", "description": "VN30, VN100, VNDIAMOND, VNFINLEAD, VNFINSELECT, VNMID, VNSML or VNX50"},
                "ticker": {"type": "string"},
                "action": {"type": "string", "enum": ["add", "remove"]},
                "effective_date": {"type": "string", "description": "YYYY-MM-DD first trading day of the new basket"},
                "quote": {"type": "string", "description": "The sentence naming the ticker, copied exactly"},
            }}}}},
    },
}

_PROMPTS = {
    "interbank": (
        "Bài báo ngày {pub}. Trích các mức lãi suất liên ngân hàng VND (bình quân liên ngân hàng do NHNN công bố hoặc "
        "mức giao dịch được báo cáo) có MỘT con số cụ thể cho kỳ hạn qua đêm, 1 tuần, 2 tuần, 1 tháng, 3 tháng. Bỏ qua "
        "khoảng ('6,8-7,9%'), lãi suất USD, lãi suất OMO/tín phiếu/tái cấp vốn, tiền gửi, cho vay, dự báo. 'date' là "
        "ngày mà mức lãi suất đó áp dụng (nếu bài nói 'kết tuần', 'phiên cuối tuần' thì là ngày cuối tuần đó; 'hôm "
        "qua' tính từ ngày bài). Không có thì trả danh sách rỗng.\n\nTiêu đề: {title}\n\n{body}"),
    "index_review": (
        "Bài báo ngày {pub}. Trích thay đổi rổ chỉ số HOSE ĐÃ CÔNG BỐ CHÍNH THỨC (HOSE/Sở GDCK TP.HCM công bố kết quả "
        "review): mã được thêm vào hoặc loại khỏi VN30, VN100, VNDiamond, VNFinLead, VNFinSelect, VNMID, VNSML, VNX50, và "
        "ngày bắt đầu áp dụng rổ mới. KHÔNG lấy dự báo của công ty chứng khoán ('dự kiến', 'có thể', 'dự báo'), không "
        "lấy danh mục ETF. Không có thì trả danh sách rỗng.\n\nTiêu đề: {title}\n\n{body}"),
}


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _pct_in(v: float, quote: str) -> bool:
    forms = {f"{v:g}".replace(".", ","), f"{v:.2f}".replace(".", ","), f"{v:.1f}".replace(".", ","), f"{v:g}"}
    return any(f in quote for f in forms)


def check_interbank(facts: list[dict], body: str, published: datetime) -> list[tuple]:
    """LLM rates → (code, date, value, quote) that pass: known tenor, rate in range, figure in the quote, quote in the
    article, date ≤ article date and ≥ 14 days before it."""
    out, text, pub_d = [], _squash(body), published.date()
    for f in facts:
        try:
            d, v, tenor, q = date.fromisoformat(str(f["date"])[:10]), float(f["rate_pct"]), f["tenor"], f["quote"]
        except (KeyError, TypeError, ValueError):
            continue
        if (tenor in TENORS and RATE_RANGE[0] <= v <= RATE_RANGE[1] and _pct_in(v, q) and _squash(q)[:60] in text
                and pub_d - timedelta(days=14) <= d <= pub_d):
            out.append((f"interbank_{TENORS[tenor]}", d, v, q[:400]))
    return out


def check_review(facts: list[dict], body: str, published: datetime, tickers: set[str]) -> list[tuple]:
    out, text, pub_d = [], _squash(body), published.date()
    for f in facts:
        try:
            idx, t, act, eff, q = (f["index"].upper().replace("-", "").replace(" ", ""), f["ticker"].upper().strip(),
                                   f["action"], date.fromisoformat(str(f["effective_date"])[:10]), f["quote"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if (idx in INDICES and t in tickers and act in ("add", "remove") and t in q.upper()
                and _squash(q)[:60] in text and pub_d - timedelta(days=3) <= eff <= pub_d + timedelta(days=60)):
            out.append((idx, t, act, eff, q[:400]))
    return out


def _ask(client, topic: str, a: dict) -> list[dict]:
    from llm.types import Message
    prompt = _PROMPTS[topic].format(pub=a["published_at"].date(), title=a["title"] or "", body=a["body"][:MAX_CHARS])
    resp = client.generate(messages=[Message(role="user", content=prompt)], tools=[_TOOLS[topic]], max_tokens=1200,
                           temperature=0)
    key = "rates" if topic == "interbank" else "changes"
    raw = resp.tool_calls[0].input.get(key) if resp.tool_calls else None
    if raw is None:
        m = re.search(r"\{.*\}", resp.text or "", re.S)
        try:
            raw = (json.loads(m.group(0)) if m else {}).get(key)
        except json.JSONDecodeError:
            raw = None
    return raw or []


def read(topics: tuple[str, ...], limit: int | None = None, workers: int = 6) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from llm import create_client

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT url, topic, published_at, title, body FROM web_articles WHERE read_at IS NULL "
                    "AND body IS NOT NULL AND published_at IS NOT NULL AND topic = ANY(%s) ORDER BY published_at",
                    (list(topics),))
        arts = [dict(zip(("url", "topic", "published_at", "title", "body"), r)) for r in cur.fetchall()]
        cur.execute("SELECT ticker FROM securities")
        tickers = {r[0] for r in cur.fetchall()}
    arts = arts[:limit] if limit else arts
    client = create_client()
    model = getattr(client, "model", None) or type(client).__name__

    def one(a: dict) -> tuple[str, int]:
        try:
            facts = _ask(client, a["topic"], a)
        except Exception:
            return "llm_error", 0
        with get_conn() as conn, conn.cursor() as cur:
            if a["topic"] == "interbank":
                rows = check_interbank(facts, a["body"], a["published_at"])
                cur.executemany("""INSERT INTO macro_indicators (code, period_end, value, published_at, source, quote)
                                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (code, period_end, source) DO UPDATE SET
                                   value=EXCLUDED.value, quote=EXCLUDED.quote, fetched_at=NOW(),
                                   published_at=LEAST(macro_indicators.published_at, EXCLUDED.published_at)""",
                                [(c, d, v, a["published_at"], "cafef", q) for c, d, v, q in rows])
            else:
                rows = check_review(facts, a["body"], a["published_at"], tickers)
                cur.executemany("""INSERT INTO index_membership_events (index_code, ticker, action, effective_date,
                                   announced_at, source, quote, model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (index_code, ticker, action, effective_date) DO UPDATE SET announced_at=LEAST(index_membership_events.announced_at,
                                   EXCLUDED.announced_at)""",
                                [(i, t, act, eff, a["published_at"], a["url"], q, model) for i, t, act, eff, q in rows])
            cur.execute("UPDATE web_articles SET read_at = NOW(), read_status = %s WHERE url = %s",
                        ("ok" if rows else "no_fact", a["url"]))
        return ("ok" if rows else "no_fact"), len(rows)

    status, stored = {}, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for st, n in pool.map(one, arts):
            status[st] = status.get(st, 0) + 1
            stored += n
    if arts and status.get("llm_error", 0) == len(arts):
        raise RuntimeError("web_facts: the LLM answered no article")
    return {"articles": len(arts), "facts": stored, "status": status}


def run(since: date | None = None, topics: tuple[str, ...] = tuple(TOPICS), limit: int | None = None) -> dict:
    since = since or date.today() - timedelta(days=10)
    return {"crawl": crawl(since, topics), "read": read(topics, limit)}


def main() -> None:
    ap = argparse.ArgumentParser(description="CafeF articles → interbank rates, index review changes")
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--topics", default=",".join(TOPICS))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--read-only", action="store_true")
    a = ap.parse_args()
    topics = tuple(t for t in a.topics.split(",") if t in TOPICS)
    print(read(topics, a.limit) if a.read_only else run(a.since, topics, a.limit))


if __name__ == "__main__":
    main()
