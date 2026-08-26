"""
data/news_scraper.py — RSS scraper for CafeF and VnExpress financial news.

Scrapes RSS summaries, cleans HTML, extracts tickers, saves to Postgres.
Does NOT do full-page HTML scraping — RSS is stable enough for summaries.

Usage:
    python data/news_scraper.py               # scrape + save to DB
    python data/news_scraper.py --dry-run     # print 5 articles, no DB writes
    python data/news_scraper.py --source cafef
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

RSS_SOURCES = [
    ("https://cafef.vn/thi-truong-chung-khoan.rss", "cafef"),
    ("https://vnexpress.net/rss/kinh-doanh.rss", "vnexpress"),
    ("https://vneconomy.vn/chung-khoan.rss", "vneconomy"),
    ("https://www.tinnhanhchungkhoan.vn/rss/home.rss", "tinnhanhchungkhoan"),
]

_TICKERS: set[str] | None = None
# High-precision: ticker in parentheses — "Hòa Phát (HPG)", "VNM (VNM)"
_PAREN_RE = re.compile(r"\(([A-Z]{2,5})\)")
# Fallback: bare ALL-CAPS word in title only (more noise)
_BARE_RE = re.compile(r"\b([A-Z]{2,5})\b")
# Common non-ticker uppercase words to exclude
_NON_TICKERS = frozenset({
    "USD", "VND", "EUR", "GBP", "JPY", "CNY",
    "GDP", "CPI", "IPO", "ETF", "CEO", "CFO", "CTO",
    "VN", "US", "UK", "EU", "FDI", "FII", "M2",
    "TV", "PC", "IT", "AI", "EV",
})
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _load_tickers() -> set[str]:
    global _TICKERS
    if _TICKERS is not None:
        return _TICKERS
    ticker_file = ROOT / "data" / "known_tickers.txt"
    if ticker_file.exists():
        _TICKERS = set(ticker_file.read_text(encoding="utf-8").splitlines())
    else:
        # Fallback — core HOSE names
        _TICKERS = {
            "HPG", "VNM", "FPT", "VIC", "MSN", "VHM", "TCB", "MBB", "VCB",
            "CTG", "BID", "VPB", "ACB", "STB", "HDB", "SSI", "VND", "HCM",
            "MWG", "DGW", "PNJ", "GAS", "PLX", "REE", "GMD", "VRE", "KDH",
        }
    return _TICKERS


def clean_html(text: str) -> str:
    text = html.unescape(text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def parse_published(entry) -> str:
    """Parse feedparser entry → UTC ISO-8601 string. Falls back to now()."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def extract_tickers(title: str, body: str = "") -> list[str]:
    """Extract stock tickers from title + body.

    Strategy:
    1. Parentheses pattern in full text: "Hòa Phát (HPG)" — high precision
    2. Bare ALL-CAPS in title only — lower precision, filtered by known list + _NON_TICKERS
    """
    known = _load_tickers()
    full_text = f"{title}\n{body}"
    found: list[str] = []

    # Step 1: parentheses pattern across full text (high precision)
    for m in _PAREN_RE.findall(full_text):
        if m in known and m not in _NON_TICKERS and m not in found:
            found.append(m)

    # Step 2: bare ALL-CAPS in title only (lower precision, only if not already found)
    for m in _BARE_RE.findall(title):
        if m in known and m not in _NON_TICKERS and m not in found:
            found.append(m)

    return found


def scrape_rss(url: str, source: str) -> list[dict]:
    try:
        import feedparser
    except ImportError:
        print("[ERROR] feedparser not installed. Run: pip install feedparser")
        return []

    try:
        feed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
    except Exception as e:
        print(f"[WARN] scrape_rss failed for {source}: {e}")
        return []

    # B2: feedparser returns empty feed (403/timeout) without raising exception
    status = getattr(feed, "status", 200)
    if status in (403, 429, 503):
        print(f"[ERROR] RSS {source} returned HTTP {status} — scraper may be blocked")
        return []
    if not feed.entries:
        print(f"[WARN] RSS {source} returned 0 entries (status={status}) — feed down or blocked")
        return []

    results = []
    for e in feed.entries:
        title = clean_html(getattr(e, "title", ""))
        if not title:
            continue

        body = clean_html(e.get("summary", ""))
        if len(body) < 80:
            body = title  # fallback: some RSS returns no summary

        link = getattr(e, "link", "")
        if not link:
            continue

        results.append({
            "url": link,
            "title": title,
            "body": body,
            "source": source,
            "published_at": parse_published(e),
            "tickers": extract_tickers(title, body),
        })
    return results


def save_article(cur, article: dict) -> bool:
    """Insert article. Returns True if new, False if already exists."""
    cur.execute(
        """
        INSERT INTO news_articles (url, title, body, source, published_at, tickers)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (url) DO NOTHING
        RETURNING id
        """,
        (
            article["url"],
            article["title"],
            article["body"],
            article["source"],
            article["published_at"],
            article["tickers"],
        ),
    )
    return cur.fetchone() is not None


def mark_indexed(cur, url: str) -> None:
    cur.execute(
        "UPDATE news_articles SET indexed_at = NOW() WHERE url = %s",
        (url,),
    )


def run_scrape(sources: list[tuple[str, str]], dry_run: bool = False) -> dict[str, dict]:
    # Returns {source: {"fetched": int, "new": int}}
    stats: dict[str, dict] = {}

    if dry_run:
        for url, source in sources:
            articles = scrape_rss(url, source)
            print(f"\n--- {source} ({len(articles)} entries) ---")
            for a in articles[:5]:
                print(f"  [{a['published_at'][:10]}] {a['title']}")
                print(f"  tickers: {a['tickers']}")
                print(f"  body[:100]: {a['body'][:100]}")
                print()
            stats[source] = {"fetched": len(articles), "new": len(articles)}
        return stats

    from data.db import get_conn

    for url, source in sources:
        articles = scrape_rss(url, source)
        new_count = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for article in articles:
                    if save_article(cur, article):
                        new_count += 1
            conn.commit()
        print(f"  {source}: {len(articles)} fetched, {new_count} new")
        stats[source] = {"fetched": len(articles), "new": new_count}
        time.sleep(1)  # polite delay between sources

    return stats


def backfill_tickers() -> int:
    """Re-extract tickers for all articles in DB using updated extract_tickers.

    Updates tickers column and resets indexed_at=NULL so news_index re-embeds
    with fresh payload. Returns count updated.
    """
    from data.db import get_conn

    updated = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT url, title, body FROM news_articles")
            rows = cur.fetchall()

        with conn.cursor() as cur:
            for url, title, body in rows:
                tickers = extract_tickers(title or "", body or "")
                cur.execute(
                    "UPDATE news_articles SET tickers = %s, indexed_at = NULL WHERE url = %s",
                    (tickers, url),
                )
                updated += 1
        conn.commit()

    print(f"  backfill done — {updated} articles updated, indexed_at reset")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="RSS news scraper")
    parser.add_argument("--dry-run", action="store_true", help="Print articles without saving")
    parser.add_argument("--source", choices=["cafef", "vnexpress", "vneconomy", "tinnhanhchungkhoan", "all"], default="all")
    parser.add_argument("--backfill-tickers", action="store_true", help="Re-extract tickers for all DB articles then reset indexed_at")
    args = parser.parse_args()

    if args.backfill_tickers:
        backfill_tickers()
        return

    sources = RSS_SOURCES
    if args.source != "all":
        sources = [(u, s) for u, s in RSS_SOURCES if s == args.source]

    stats = run_scrape(sources, dry_run=args.dry_run)
    total_new = sum(s["new"] for s in stats.values())
    print(f"\nTotal new articles: {total_new}")

    failed = [src for src, s in stats.items() if s["fetched"] == 0]
    if failed:
        print(f"[ERROR] Sources returned 0 articles: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
