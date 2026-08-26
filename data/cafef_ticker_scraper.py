"""
data/cafef_ticker_scraper.py — Scrape corporate events/news from cafef per ticker.

Source: https://cafef.vn/du-lieu/tin-doanh-nghiep/{ticker_lower}/event.chn
Content: BCTC, HĐQT resolutions, quarterly results — guaranteed ticker-tagged.

Usage:
    python data/cafef_ticker_scraper.py --ticker HPG
    python data/cafef_ticker_scraper.py --ticker HPG --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
_BASE = "https://cafef.vn"
_LIST_URL = _BASE + "/du-lieu/tin-doanh-nghiep/{ticker_lower}/event.chn"
_TIMEOUT = 12


def _parse_date(text: str) -> str:
    """Extract DD/MM/YYYY from li text → UTC ISO-8601. Falls back to now()."""
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _fetch_body(url: str) -> str:
    """Fetch article page and return cleaned body text."""
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] fetch body failed {url[:70]}: {e}")
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one(".content")
    if not el:
        return ""
    return el.get_text(separator=" ", strip=True)


def scrape_ticker(ticker: str, max_articles: int = 20) -> list[dict]:
    """Scrape cafef du-lieu events for ticker. Returns list of article dicts."""
    t = ticker.strip().upper()
    list_url = _LIST_URL.format(ticker_lower=t.lower())

    try:
        r = httpx.get(list_url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        print(f"[cafef] fetch list failed for {t}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    seen_hrefs: set[str] = set()
    articles: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        title = a.text.strip()
        # Only du-lieu ticker articles
        if not re.match(r"^/du-lieu/" + re.escape(t) + r"-\d+/", href):
            continue
        # Strip utm params
        clean_href = href.split("?")[0]
        if clean_href in seen_hrefs or len(title) < 5:
            continue
        seen_hrefs.add(clean_href)

        li = a.find_parent("li")
        pub_date = _parse_date(li.text if li else "")

        articles.append({
            "href": clean_href,
            "title": title,
            "pub": pub_date,
        })
        if len(articles) >= max_articles:
            break

    if not articles:
        print(
            f"[cafef] WARNING: 0 articles found for {t}. "
            f"URL may have changed: {list_url}"
        )

    results: list[dict] = []
    for item in articles:
        url = _BASE + item["href"]
        body = _fetch_body(url)
        if not body:
            continue
        results.append({
            "url":          url,
            "title":        item["title"],
            "body":         body,
            "source":       "cafef",
            "published_at": item["pub"],
            "tickers":      [t],
        })
        time.sleep(0.5)  # polite delay

    return results


def fetch_and_save(ticker: str, dry_run: bool = False) -> int:
    """Scrape ticker articles, save to news_articles DB. Returns new count."""
    articles = scrape_ticker(ticker)
    if not articles:
        print(f"[cafef] no articles found for {ticker}")
        return 0

    if dry_run:
        for a in articles:
            print(f"  [{a['published_at'][:10]}] {a['title'][:70]}")
            print(f"  tickers={a['tickers']} body={a['body'][:80]}...")
        return len(articles)

    from data.db import get_conn
    from data.news_scraper import save_article

    new_count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for article in articles:
                if save_article(cur, article):
                    new_count += 1
        conn.commit()

    print(f"[cafef] {ticker}: {len(articles)} fetched, {new_count} new")
    return new_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Cafef ticker news scraper")
    parser.add_argument("--ticker", required=True, help="Stock ticker e.g. HPG")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    fetch_and_save(args.ticker, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
