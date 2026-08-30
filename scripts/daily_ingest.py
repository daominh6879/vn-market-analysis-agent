"""
scripts/daily_ingest.py — One-command daily data refresh for all active securities.

Runs every table's incremental fetch in order:
  1. ohlcv_daily + foreign_flows (Fireant, single pass per ticker)
  2. market_index_daily (VCI index provider)
  3. market_quotes (global quotes — commodity/fx/crypto)

For initial 1-year backfill, use --migrate instead:
    python scripts/daily_ingest.py --migrate

Normal daily cron:
    python scripts/daily_ingest.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYTHON = sys.executable


def run(cmd: list[str], label: str) -> bool:
    print(f"\n>>> {label}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"    FAILED (exit {result.returncode})", file=sys.stderr)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily DB refresh for all active securities")
    parser.add_argument("--migrate", action="store_true",
                        help="Initial 1-year backfill instead of daily incremental")
    args = parser.parse_args()

    today = str(date.today())
    ok = True

    if args.migrate:
        print(f"=== MIGRATE — 1-year backfill — {today} ===")

        # OHLCV + foreign flows (combined via Fireant)
        ok &= run(
            [PYTHON, "ingest/fetch_ohlcv.py", "--migrate"],
            "OHLCV + foreign flows (Fireant 1-year backfill)",
        )

        # Market indices
        ok &= run(
            [PYTHON, "ingest/fetch_index.py", "--days", "365"],
            "Market index daily (365 days)",
        )

    else:
        print(f"=== DAILY INGEST — {today} ===")

        # OHLCV + foreign flows (incremental, all active tickers)
        ok &= run(
            [PYTHON, "ingest/fetch_ohlcv.py", "--all-securities"],
            "OHLCV + foreign flows (incremental)",
        )

        # Market indices (last 5 days to catch any gaps)
        ok &= run(
            [PYTHON, "ingest/fetch_index.py", "--days", "5"],
            "Market index daily",
        )

    # Audit at end
    ok &= run(
        [PYTHON, "scripts/audit_db.py"],
        "DB audit",
    )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
