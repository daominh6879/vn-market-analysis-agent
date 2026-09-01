"""
data/known_tickers_seed.py — Verify ticker coverage from the securities table.

Previously wrote known_tickers.txt; that file is no longer used.
news_scraper.py reads directly from the securities table via core.tickers.get_tickers().

Run this to check how many tickers are in the securities table:
    python data/known_tickers_seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass


def main() -> None:
    from core.tickers import get_tickers
    tickers = get_tickers()
    print(f"securities table: {len(tickers)} active tickers")
    if tickers:
        print(f"  sample: {tickers[:10]}")


if __name__ == "__main__":
    main()
