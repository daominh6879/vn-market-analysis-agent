"""
ingest/fetch_corporate_events.py — Scrape CafeF/TCBS corporate events → corporate_events.

Thin wrapper over data.corporate_events_scraper.
Mirrors the Dagster asset corporate_events_ingest (pipeline/assets_market_brief.py).

Usage:
    python ingest/fetch_corporate_events.py
    python ingest/fetch_corporate_events.py --days-back 1 --days-ahead 14
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from data.corporate_events_scraper import cleanup_old_events, scrape_events, upsert_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape corporate events → corporate_events")
    parser.add_argument("--days-back",  type=int, default=1)
    parser.add_argument("--days-ahead", type=int, default=14)
    args = parser.parse_args()

    deleted = cleanup_old_events(days_keep=30)
    print(f"corporate_events: cleaned {deleted} old rows")

    start = date.today() - timedelta(days=args.days_back)
    end = date.today() + timedelta(days=args.days_ahead)
    print(f"corporate_events: scraping {start} → {end}")

    events = scrape_events(start, end)
    print(f"corporate_events: scraped {len(events)} events")

    n = upsert_events(events)
    print(f"corporate_events done — {n} rows upserted")


if __name__ == "__main__":
    main()
