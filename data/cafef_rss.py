"""
data/cafef_rss.py — Vietnamese financial news via RSS + HTML scraper.

Fetch priority:
  1. RSS feeds — CafeF (market news + corporate), Tinnhanhchungkhoan
  2. HTML scraper — CafeF stock market page (fallback when RSS < min_articles)

Returns list[dict] with keys: title, url, source, published_at
Format matches Tavily article dicts — drop-in compatible.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_RSS_FEEDS = [
    ("https://cafef.vn/thi-truong-chung-khoan.rss", "cafef"),
    ("https://cafef.vn/doanh-nghiep.rss",            "cafef"),
]

_CAFEF_MARKET_URL = "https://cafef.vn/thi-truong-chung-khoan.chn"

# Noise patterns to skip (price-history pages, company profiles, etc.)
_NOISE_URL_PATTERNS = (
    "lich-su-giao-dich", "ho-so-doanh-nghiep", "bang-gia",
    "bao-cao-tai-chinh", "/data/", "sitemap", "du-lieu.chn",
)


# ── Date parsing ──────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[datetime]:
    """Parse RFC 2822 or ISO 8601 date string into timezone-aware datetime."""
    if not raw:
        return None
    raw = raw.strip()

    # RFC 2822 — standard RSS pubDate: "Thu, 28 Aug 2026 06:30:00 +0700"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    # ISO 8601
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass

    return None


# ── RSS parser ────────────────────────────────────────────────────────────────

def _parse_rss(xml_text: str, source: str, cutoff: datetime) -> list[dict]:
    """Parse RSS 2.0 XML string, return articles newer than cutoff."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        sys.stderr.write(f"[cafef_rss] RSS parse error ({source}): {e}\n")
        return []

    articles = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        # CafeF wraps titles in CDATA — ET strips it automatically
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        if any(p in url.lower() for p in _NOISE_URL_PATTERNS):
            continue

        pub_raw = item.findtext("pubDate") or ""
        pub_dt = _parse_date(pub_raw)
        if pub_dt and pub_dt < cutoff:
            continue

        pub_iso = pub_dt.isoformat() if pub_dt else datetime.now(timezone.utc).isoformat()
        articles.append({
            "title":        title,
            "url":          url,
            "source":       source,
            "published_at": pub_iso,
        })

    return articles


# ── RSS fetch ─────────────────────────────────────────────────────────────────

def fetch_rss_news(target_date: Optional[str] = None, max_age_hours: int = 36) -> list[dict]:
    """
    Fetch all RSS feeds in parallel and return deduped list sorted by recency.
    target_date: YYYY-MM-DD cap — articles older than max_age_hours before midnight
                 of target_date are dropped.
    """
    try:
        import httpx
    except ImportError:
        sys.stderr.write("[cafef_rss] httpx not installed\n")
        return []

    if target_date:
        try:
            base = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # Allow articles up to max_age_hours before the date
            cutoff = base - timedelta(hours=max_age_hours)
        except Exception:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    def _fetch_one(feed_url: str, source: str) -> list[dict]:
        try:
            resp = httpx.get(
                feed_url,
                timeout=12,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; VNMarketBrief/1.0)",
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
                follow_redirects=True,
            )
            if resp.status_code != 200:
                sys.stderr.write(f"[cafef_rss] {feed_url} → HTTP {resp.status_code}\n")
                return []
            return _parse_rss(resp.text, source, cutoff)
        except Exception as e:
            sys.stderr.write(f"[cafef_rss] {feed_url} failed: {e}\n")
            return []

    all_articles: list[dict] = []
    seen_urls: set = set()

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_one, url, src): src for url, src in _RSS_FEEDS}
        for fut in as_completed(futures):
            for art in (fut.result() or []):
                if art["url"] not in seen_urls:
                    seen_urls.add(art["url"])
                    all_articles.append(art)

    all_articles.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return all_articles


# ── HTML scraper (Option A fallback) ─────────────────────────────────────────

def scrape_cafef_news(target_date: Optional[str] = None, max_articles: int = 12) -> list[dict]:
    """
    Fallback: scrape CafeF stock market page for article links + titles.
    CafeF article URLs embed date: /section/title-YYYYMMDDHHMMSS###.chn
    """
    try:
        import httpx
    except ImportError:
        return []

    try:
        resp = httpx.get(
            _CAFEF_MARKET_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            follow_redirects=True,
        )
        if resp.status_code != 200:
            sys.stderr.write(f"[cafef_rss] scrape → HTTP {resp.status_code}\n")
            return []
        html = resp.text
    except Exception as e:
        sys.stderr.write(f"[cafef_rss] scrape failed: {e}\n")
        return []

    articles: list[dict] = []
    seen_urls: set = set()

    # Compute date cutoff — same contract as fetch_rss_news (max_age_hours=24 default)
    max_age_hours = 24
    cutoff_dt = (
        datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc) - timedelta(hours=max_age_hours)
        if target_date
        else datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    )

    # CafeF article URLs contain "-188YYMMDDHHMMSS\d+.chn"
    # Category/section pages do NOT contain "-188" — filter those out.
    pattern = re.compile(
        r'href="(/[^"]*-188[^"]+\.chn)"[^>]*title="([^"]{25,250})"',
        re.IGNORECASE,
    )
    for href, title_raw in pattern.findall(html):
        if any(p in href.lower() for p in _NOISE_URL_PATTERNS):
            continue
        import html as _html
        title = _html.unescape(re.sub(r"\s+", " ", title_raw).strip())
        if len(title) < 25:
            continue

        url = f"https://cafef.vn{href}"
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # CafeF URL date format: -188YYMMDDHHMMSS\d+.chn
        # Capture 12 digits after "188": YYMMDDHHMMSS → prepend "20" → YYYYMMDDHHMMSS
        pub_iso = datetime.now(timezone.utc).isoformat()
        parsed_dt: Optional[datetime] = None
        date_match = re.search(r"-188(\d{12})\d*\.chn", href)
        if date_match:
            try:
                stamp = "20" + date_match.group(1)  # "20" + "YYMMDDHHMMSS"
                dt = datetime.strptime(stamp, "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))  # UTC+7 VN
                pub_iso = dt.isoformat()
                parsed_dt = dt
            except Exception:
                pass

        # Apply date cutoff — skip articles older than max_age_hours
        if parsed_dt and parsed_dt < cutoff_dt:
            continue

        articles.append({"title": title, "url": url, "source": "cafef", "published_at": pub_iso})
        if len(articles) >= max_articles:
            break

    return articles


# ── Ticker-specific news scraper ─────────────────────────────────────────────

_CAFEF_SEARCH_URL = "https://cafef.vn/tim-kiem.chn"

def fetch_ticker_news(ticker: str, max_articles: int = 10) -> list[dict]:
    """
    Scrape CafeF search for ticker-specific news articles.

    Uses CafeF's search page: cafef.vn/tim-kiem.chn?keywords={ticker}
    Extracts article links via title attribute pattern (same as market scraper).
    Returns list[dict] with keys: title, url, source, published_at — compatible
    with fetch_vn_market_news() output.
    """
    try:
        import httpx
    except ImportError:
        return []

    try:
        resp = httpx.get(
            _CAFEF_SEARCH_URL,
            params={"keywords": ticker.upper(), "channelid": "0"},
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://cafef.vn/",
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            sys.stderr.write(f"[cafef_ticker] search {ticker} → HTTP {resp.status_code}\n")
            return []
        html = resp.text
    except Exception as e:
        sys.stderr.write(f"[cafef_ticker] search {ticker} failed: {e}\n")
        return []

    import html as _html

    articles: list[dict] = []
    seen_urls: set = set()

    pattern = re.compile(
        r'href="(/[^"]*-188[^"]+\.chn)"[^>]*title="([^"]{20,250})"',
        re.IGNORECASE,
    )
    for href, title_raw in pattern.findall(html):
        if any(p in href.lower() for p in _NOISE_URL_PATTERNS):
            continue
        title = _html.unescape(re.sub(r"\s+", " ", title_raw).strip())
        if len(title) < 20:
            continue

        url = f"https://cafef.vn{href}"
        if url in seen_urls:
            continue
        seen_urls.add(url)

        pub_iso = datetime.now(timezone.utc).isoformat()
        date_match = re.search(r"-188(\d{12})\d*\.chn", href)
        if date_match:
            try:
                stamp = "20" + date_match.group(1)
                dt = datetime.strptime(stamp, "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))
                pub_iso = dt.isoformat()
            except Exception:
                pass

        articles.append({"title": title, "url": url, "source": "cafef", "published_at": pub_iso})
        if len(articles) >= max_articles:
            break

    return articles


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_vn_market_news(
    target_date: Optional[str] = None,
    min_articles: int = 4,
    max_total: int = 10,
) -> list[dict]:
    """
    Fetch fresh Vietnamese financial news.
    1. RSS feeds (CafeF stock + corporate, Tinnhanhchungkhoan)
    2. If < min_articles from RSS → HTML scraper on CafeF market page

    Returns deduped list sorted by recency, up to max_total items.
    """
    articles = fetch_rss_news(target_date=target_date)

    if len(articles) < min_articles:
        sys.stderr.write(
            f"[cafef_rss] RSS returned {len(articles)} articles "
            f"(< {min_articles}) — trying HTML scraper\n"
        )
        scraped = scrape_cafef_news(target_date=target_date)
        seen_urls = {a["url"] for a in articles}
        for art in scraped:
            if art["url"] not in seen_urls:
                articles.append(art)
                seen_urls.add(art["url"])

    articles.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return articles[:max_total]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="YYYY-MM-DD target date")
    parser.add_argument("--rss-only", action="store_true")
    parser.add_argument("--scrape-only", action="store_true")
    args = parser.parse_args()

    if args.rss_only:
        results = fetch_rss_news(target_date=args.date)
        print(f"\n=== RSS only: {len(results)} articles ===")
    elif args.scrape_only:
        results = scrape_cafef_news(target_date=args.date)
        print(f"\n=== Scraper only: {len(results)} articles ===")
    else:
        results = fetch_vn_market_news(target_date=args.date)
        print(f"\n=== Combined: {len(results)} articles ===")

    for i, a in enumerate(results, 1):
        print(f"{i:2}. [{a['source']} | {a['published_at'][:16]}] {a['title']}")
