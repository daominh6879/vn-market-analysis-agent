"""scripts/sync_bctc.py — thin CLI wrapper; the logic lives in ingest/sync_bctc.py (scheduled by Dagster `bctc_sync`)."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ingest.sync_bctc import main

if __name__ == "__main__":
    main()
