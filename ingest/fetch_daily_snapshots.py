"""
ingest/fetch_daily_snapshots.py — Save today's SJC gold, SBV central rate and VCB USD rate
into daily_snapshots (idempotent per day) so day-over-day questions can be answered.

Usage:
    python ingest/fetch_daily_snapshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from data.snapshots import save_snapshot


def run() -> tuple[list[str], list[str]]:
    """Save today's snapshots. Returns (saved series, unavailable series)."""
    saved, missing = [], []

    from data.gold_vn_scraper import fetch_sjc_gold
    gold = fetch_sjc_gold()
    # The yfinance fallback is a world-price estimate, not SJC — never store it as SJC.
    if gold and not str(gold.get("source", "")).startswith("world_estimate"):
        if save_snapshot("sjc_gold", buy=gold["buy_vnd"], sell=gold["sell_vnd"],
                         unit="triệu đồng/lượng", source=gold["source"]):
            saved.append("sjc_gold")
    else:
        missing.append("sjc_gold")

    from data.fx_scraper import fetch_vcb_usdvnd, fetch_sbv_central_rate
    vcb = fetch_vcb_usdvnd()
    if vcb and save_snapshot("vcb_usd", buy=vcb["buy"], sell=vcb["sell"], unit="VND/USD", source="vietcombank"):
        saved.append("vcb_usd")
    else:
        missing.append("vcb_usd")

    central = fetch_sbv_central_rate()
    if central and save_snapshot("sbv_central_rate", value=central, unit="VND/USD", source="sbv"):
        saved.append("sbv_central_rate")
    else:
        missing.append("sbv_central_rate")

    return saved, missing


def main() -> None:
    saved, missing = run()
    print(f"daily_snapshots saved: {', '.join(saved) or '-'}")
    if missing:
        print(f"daily_snapshots unavailable: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
