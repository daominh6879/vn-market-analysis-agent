"""
scripts/fix_fpt_ticker.py — Remove FPT chunks wrongly tagged as HPG, re-index as FPT.

What happened:
  - 183 chunks from FPT BCTC PDF were indexed with ticker="HPG" (wrong config)
  - FPT has 0 correct chunks in Qdrant → FPT queries return empty
  - HPG queries return FPT financial data → LLM says "Không có trong tài liệu"

What this script does:
  1. Deletes 183 points with source_key=FPT_PDF but ticker=HPG
  2. Reads FPT parsed .md from MinIO (fpt-docs)
  3. Re-indexes with ticker="FPT", sector from securities DB

Usage:
    python scripts/fix_fpt_ticker.py --dry-run   # print plan only
    python scripts/fix_fpt_ticker.py             # execute fix
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

FPT_SOURCE_KEY = "2025/20260319_-_FPT_-_BCTC_rieng_nam_2025_da_kiem_toan_1773994892.pdf"
FPT_MD_KEY     = "parsed/2025/20260319_-_FPT_-_BCTC_rieng_nam_2025_da_kiem_toan_1773994892.md"
FPT_BUCKET     = "fpt-docs"
COLLECTION     = "bctc_structural"


def _minio():
    from minio import Minio
    endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000").replace("http://", "").replace("https://", "")
    return Minio(
        endpoint,
        access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )


def _sector(ticker: str) -> str:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sector FROM securities WHERE ticker = %s", (ticker.upper(),))
                row = cur.fetchone()
                return row[0].lower() if row and row[0] else "công nghệ"
    except Exception:
        return "công nghệ"


def step1_delete_contaminated(dry: bool) -> int:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    qc = QdrantClient("localhost", port=6333)
    f = Filter(must=[FieldCondition(key="source_key", match=MatchValue(value=FPT_SOURCE_KEY))])
    cnt = qc.count(COLLECTION, count_filter=f)
    print(f"  Found {cnt.count} contaminated chunks (source={FPT_SOURCE_KEY})")

    if dry:
        print("  [DRY RUN] skip delete")
        return cnt.count

    qc.delete(COLLECTION, points_selector=f)
    after = qc.count(COLLECTION, count_filter=f)
    print(f"  Deleted. Remaining: {after.count}")
    return cnt.count


def step2_reindex_fpt(dry: bool) -> int:
    mc = _minio()
    print(f"  Reading {FPT_BUCKET}/{FPT_MD_KEY}")
    try:
        obj = mc.get_object(FPT_BUCKET, FPT_MD_KEY)
        content = obj.read().decode("utf-8", errors="replace")
        print(f"  Read {len(content):,} chars")
    except Exception as exc:
        print(f"  ERROR reading FPT .md: {exc}")
        return 0

    if dry:
        print("  [DRY RUN] skip index")
        return 0

    from rag.index import run as index_run
    from qdrant_client import QdrantClient

    qc = QdrantClient("localhost", port=6333)
    sector = _sector("FPT")
    meta = {
        "ticker": "FPT",
        "sector": sector,
        "year": "2025",
        "report_type": "annual",
        "source_key": FPT_SOURCE_KEY,
        "dagster_run_id": "manual_fix",
    }
    embed_model = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
    n = index_run(
        text=content,
        collection=COLLECTION,
        strategy="structural",
        embed_model=embed_model,
        meta=meta,
        client=qc,
    )
    print(f"  Indexed {n} FPT chunks with ticker=FPT")
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry = args.dry_run

    print("=" * 60)
    print("FIX FPT TICKER CONTAMINATION")
    if dry:
        print("*** DRY RUN ***")
    print("=" * 60)

    print("\n[Step 1] Delete FPT-source chunks tagged as HPG")
    deleted = step1_delete_contaminated(dry)

    print("\n[Step 2] Re-index FPT with ticker=FPT")
    indexed = step2_reindex_fpt(dry)

    print("\n" + "=" * 60)
    if dry:
        print(f"DRY RUN: would delete {deleted} chunks, then index FPT docs")
    else:
        print(f"DONE: deleted {deleted} contaminated chunks, indexed {indexed} FPT chunks")
    print("=" * 60)


if __name__ == "__main__":
    main()
