"""
scripts/reset_bctc.py — Reset toàn bộ BCTC data: MinIO + Qdrant + Postgres.

**WARNING: DESTRUCTIVE**
  - Xóa tất cả bucket *-docs trong MinIO (hpg-docs, vcb-docs, ...)
  - Xóa Qdrant collections: bctc_structural + legacy (hpg_structural, hpg_b7_*)
  - TRUNCATE Postgres: documents, financial_facts, quarantine_log

Usage:
    python scripts/reset_bctc.py --dry-run   # print plan only
    python scripts/reset_bctc.py             # execute reset
    python scripts/reset_bctc.py --skip-minio   # keep MinIO, reset only Qdrant+Postgres
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_USER     = os.getenv("MINIO_ROOT_USER", "minioadmin")
MINIO_PASS     = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")

# Collections to delete (legacy + current)
QDRANT_COLLECTIONS_TO_DELETE = [
    "bctc_structural",
    "hpg_structural",
    "hpg_b7_structural_meta",
    "hpg_b7_structural_nometa",
    "hpg_b7_fixed_meta",
    "hpg_b7_fixed_nometa",
    "hpg_b7_hier_meta",
    "hpg_b7_hier_nometa",
]

POSTGRES_TABLES = ["financial_facts", "quarantine_log", "documents"]


# ── MinIO ─────────────────────────────────────────────────────────────────────

def reset_minio(dry: bool) -> None:
    print("\n[1] MinIO")
    try:
        from minio import Minio
    except ImportError:
        print("  minio not installed — skip")
        return

    url = MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
    secure = MINIO_ENDPOINT.startswith("https://")
    client = Minio(url, access_key=MINIO_USER, secret_key=MINIO_PASS, secure=secure)

    try:
        buckets = [b.name for b in client.list_buckets() if b.name.endswith("-docs")]
    except Exception as exc:
        print(f"  ERROR connecting to MinIO: {exc}")
        return

    if not buckets:
        print("  No *-docs buckets found — nothing to delete")
        return

    for bucket in buckets:
        objects = list(client.list_objects(bucket, recursive=True))
        print(f"  Bucket: {bucket}  ({len(objects)} objects)")
        if dry:
            continue
        for obj in objects:
            client.remove_object(bucket, obj.object_name)
        client.remove_bucket(bucket)
        print(f"  Deleted: {bucket}")

    if dry:
        print("  [DRY RUN] skip delete")


# ── Qdrant ────────────────────────────────────────────────────────────────────

def reset_qdrant(dry: bool) -> None:
    print("\n[2] Qdrant")
    from qdrant_client import QdrantClient
    client = QdrantClient("localhost", port=6333)

    existing = {c.name for c in client.get_collections().collections}
    to_delete = [n for n in QDRANT_COLLECTIONS_TO_DELETE if n in existing]
    unknown   = existing - set(QDRANT_COLLECTIONS_TO_DELETE)

    if not to_delete:
        print("  No target collections found — nothing to delete")
    else:
        for name in to_delete:
            info = client.get_collection(name)
            print(f"  {name}  ({info.points_count} points)")
            if not dry:
                client.delete_collection(name)
                print(f"    deleted")

    if unknown:
        print(f"  Skipped (not in delete list): {sorted(unknown)}")

    if dry:
        print("  [DRY RUN] skip delete")


# ── Postgres ──────────────────────────────────────────────────────────────────

def reset_postgres(dry: bool) -> None:
    print("\n[3] Postgres")
    try:
        from core.db import get_conn
    except ImportError:
        try:
            from data.db import get_conn
        except ImportError:
            print("  db module not found — skip")
            return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in POSTGRES_TABLES:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
                    (table,),
                )
                if not cur.fetchone()[0]:
                    print(f"  {table}: does not exist — skip")
                    continue
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                n = cur.fetchone()[0]
                print(f"  {table}: {n} rows")
                if not dry:
                    cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
                    print(f"    truncated")

        if dry:
            print("  [DRY RUN] skip truncate")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Reset BCTC data")
    parser.add_argument("--dry-run",    action="store_true", help="Print plan, no changes")
    parser.add_argument("--skip-minio", action="store_true", help="Skip MinIO reset")
    args = parser.parse_args()

    dry = args.dry_run

    print("=" * 60)
    print("RESET BCTC DATA")
    if dry:
        print("*** DRY RUN — no changes will be made ***")
    print("=" * 60)

    if not args.skip_minio:
        reset_minio(dry)
    else:
        print("\n[1] MinIO — SKIPPED (--skip-minio)")

    reset_qdrant(dry)
    reset_postgres(dry)

    print("\n" + "=" * 60)
    if dry:
        print("DRY RUN COMPLETE — run without --dry-run to execute")
    else:
        print("RESET COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
