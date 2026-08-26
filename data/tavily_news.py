"""
data/tavily_news.py — Tavily web search for ticker-specific news.

Searches "{ticker} cổ phiếu tin tức" to find recent VN financial news
that mention the ticker explicitly. Results tagged tickers=[ticker].

Usage:
    python data/tavily_news.py --ticker HPG --days 7
    python data/tavily_news.py --ticker HPG --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass


# Vietnamese financial news domains — restrict search for VN tickers
_VN_FINANCE_DOMAINS = [
    "cafef.vn",
    "vietstock.vn",
    "tinnhanhchungkhoan.vn",
    "vnexpress.net",
    "vneconomy.vn",
    "theinvestor.vn",
    "ndh.vn",
    "baodautu.vn",
]


def _is_vn_ticker(ticker: str) -> bool:
    """2–4 ALL-CAPS chars, no dot → likely VN stock."""
    t = ticker.strip().upper()
    return "." not in t and 2 <= len(t) <= 4


def search_ticker_news(ticker: str, days: int = 7, max_results: int = 10) -> list[dict]:
    """Search Tavily for recent news about ticker. Returns article dicts."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        print("[tavily] TAVILY_API_KEY not set in .env")
        return []

    try:
        from tavily import TavilyClient
    except ImportError:
        print("[tavily] tavily-python not installed. Run: pip install tavily-python")
        return []

    t = ticker.strip().upper()
    client = TavilyClient(api_key=api_key)

    vn = _is_vn_ticker(t)
    query = f"{t} cổ phiếu tin tức tài chính" if vn else f"{t} stock news financial"

    search_kwargs: dict = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_raw_content": False,
        "topic": "news",
    }
    if vn:
        search_kwargs["include_domains"] = _VN_FINANCE_DOMAINS

    try:
        response = client.search(**search_kwargs)
    except Exception as e:
        print(f"[tavily] search failed for {t}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles: list[dict] = []

    for item in response.get("results", []):
        url = item.get("url", "")
        title = (item.get("title") or "").strip()
        body = (item.get("content") or "").strip()
        if not url or not title:
            continue

        # Filter non-article pages: price history, company profiles, index pages
        _NOISE_PATTERNS = (
            "lich-su-giao-dich", "ho-so-doanh-nghiep", "du-lieu.chn",
            "bang-gia", "bao-cao-tai-chinh", "/data/", "sitemap",
        )
        if any(p in url.lower() for p in _NOISE_PATTERNS):
            continue
        # Require minimum body length — skip stubs and category pages
        if len(body) < 100:
            continue

        # Parse published date if available
        pub_raw = item.get("published_date") or ""
        pub_date: str
        if pub_raw:
            try:
                dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue  # skip articles older than days window
                pub_date = dt.isoformat()
            except ValueError:
                pub_date = datetime.now(timezone.utc).isoformat()
        else:
            pub_date = datetime.now(timezone.utc).isoformat()

        articles.append({
            "url":          url,
            "title":        title,
            "body":         body,
            "source":       "tavily",
            "published_at": pub_date,
            "tickers":      [t],
        })

    return articles


def fetch_and_save(ticker: str, days: int = 7, dry_run: bool = False) -> int:
    """Search Tavily for ticker news, save to news_articles DB. Returns new count."""
    articles = search_ticker_news(ticker, days=days)
    if not articles:
        print(f"[tavily] no articles found for {ticker}")
        return 0

    if dry_run:
        for a in articles:
            print(f"  [{a['published_at'][:10]}] {a['title'][:70]}")
            print(f"  tickers={a['tickers']} url={a['url'][:60]}")
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

    print(f"[tavily] {ticker}: {len(articles)} fetched, {new_count} new")
    return new_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Tavily ticker news search")
    parser.add_argument("--ticker", required=True, help="Stock ticker e.g. HPG")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    fetch_and_save(args.ticker, days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
