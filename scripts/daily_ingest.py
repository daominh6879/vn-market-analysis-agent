"""
scripts/daily_ingest.py — One-command daily data refresh for all active securities.

Runs every table's incremental fetch in order:
  1. ohlcv_daily + foreign_flows (Fireant, single pass per ticker)
  2. foreign_flows latest session (VCI price board — real traded value)
  3. market_index_daily (VCI index provider)
  4. market_quotes (global quotes — commodity/fx/crypto)

Step 1 derives foreign value from volume x close because Fireant only exposes
foreign volume, and it writes with ON CONFLICT DO NOTHING so it never clobbers
a better source. Step 2 is that better source: VCI reports the real traded
value, but only for the current session. Mirrors the Dagster pair
foreign_flows_1730 (VCI) / ohlcv_daily_1830 (Fireant).

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


def run_foreign_live() -> None:
    """Upgrade the latest session to VCI's real traded values. Never fatal.

    VCI is the only working foreign provider (see BLOCKED.md), and it writes
    nothing at all on weekends and holidays by design. The derived values from
    the Fireant pass are already in place, so a failure here costs accuracy on
    one session, not completeness — it must not fail the whole daily run.
    """
    if not run(
        [PYTHON, "ingest/fetch_foreign_flows.py", "--all-securities", "--live"],
        "foreign_flows latest session (VCI price board)",
    ):
        print("    WARNING: VCI live fetch failed — keeping derived Fireant values",
              file=sys.stderr)


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

        # Latest session gets VCI's real traded values (non-fatal)
        run_foreign_live()

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

        # Latest session gets VCI's real traded values (non-fatal)
        run_foreign_live()

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
