"""
data/reconcile.py — Bài 10: Đối chiếu Postgres vs Qdrant.

So sánh:
  - pg_active:   doc_id có status='active' trong Postgres
  - qdrant_ids:  doc_id xuất hiện trong Qdrant (scroll tất cả vector)

Báo cáo:
  - orphan_in_qdrant:  Có trong Qdrant nhưng không active trong Postgres → rác
  - missing_in_qdrant: Active trong Postgres nhưng không có chunk trong Qdrant → lệch

--fix: tự xoá orphan khỏi Qdrant

Usage:
    python data/reconcile.py --collection hpg_structural
    python data/reconcile.py --collection hpg_structural --fix
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from data.db import get_conn

QDRANT_URL = "http://localhost:6333"


def get_active_pg_ids(collection: str) -> set[str]:
    """Lấy tất cả doc_id active trong Postgres cho collection này."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id FROM documents WHERE status='active' AND collection=%s",
                (collection,),
            )
            return {row[0] for row in cur.fetchall()}


def scroll_all_doc_ids(qdrant: QdrantClient, collection: str) -> set[str]:
    """Scroll toàn bộ Qdrant collection, thu thập doc_id từ payload."""
    doc_ids: list[str] = []
    offset = None
    while True:
        result, next_offset = qdrant.scroll(
            collection_name=collection,
            with_payload=["doc_id"],
            with_vectors=False,
            limit=500,
            offset=offset,
        )
        for point in result:
            did = (point.payload or {}).get("doc_id")
            if did:
                doc_ids.append(did)
        if next_offset is None:
            break
        offset = next_offset
    return set(doc_ids)


def reconcile(collection: str, fix: bool = False) -> dict:
    qdrant = QdrantClient("localhost", port=6333)

    print(f"Đối chiếu collection: {collection}")
    pg_ids = get_active_pg_ids(collection)
    qdrant_ids = scroll_all_doc_ids(qdrant, collection)

    orphan = qdrant_ids - pg_ids
    missing = pg_ids - qdrant_ids

    print(f"  active trong Postgres : {len(pg_ids)}")
    print(f"  doc_id trong Qdrant   : {len(qdrant_ids)}")
    print(f"  orphan in Qdrant      : {len(orphan)}  {sorted(orphan)}")
    print(f"  missing in Qdrant     : {len(missing)}  {sorted(missing)}")

    if fix and orphan:
        for doc_id in orphan:
            qdrant.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
            )
            print(f"  [fix] xoá orphan doc_id={doc_id}")
        print("fix done")

    return {"orphan": list(orphan), "missing": list(missing)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Đối chiếu Postgres vs Qdrant")
    parser.add_argument(
        "--collection", default="hpg_structural", help="Qdrant collection"
    )
    parser.add_argument("--fix", action="store_true", help="Tự xoá orphan khỏi Qdrant")
    args = parser.parse_args()
    reconcile(args.collection, fix=args.fix)


if __name__ == "__main__":
    main()
