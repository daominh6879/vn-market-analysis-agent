"""
data/delete.py — Bài 10: Xoá tài liệu (soft delete).

Soft delete = 2 bước atomic:
  1. Đánh dấu status='deleted' trong Postgres (kiểm toán)
  2. Xoá chunk khỏi Qdrant (không trả lời từ doc đã thu hồi)

Nếu bước 2 fail → rollback Postgres → không lệch.

Usage:
    python data/delete.py --doc-id <doc_id> --collection hpg_structural
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


def soft_delete(doc_id: str, collection: str) -> None:
    """
    Xoá 1 tài liệu: đánh dấu Postgres + xoá Qdrant chunks.
    Atomic: Postgres rollback nếu Qdrant fail.
    """
    qdrant = QdrantClient("localhost", port=6333)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Kiểm tra doc tồn tại và đang active
            cur.execute(
                "SELECT doc_id, status FROM documents WHERE doc_id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"doc_id '{doc_id}' không có trong bảng documents")
            if row[1] == "deleted":
                print(f"doc_id '{doc_id}' đã bị xoá trước đó — bỏ qua")
                return

            # Bước 1: đánh dấu deleted trong Postgres (chưa commit)
            cur.execute(
                "UPDATE documents SET status='deleted', deleted_at=NOW() WHERE doc_id=%s",
                (doc_id,),
            )

            # Bước 2: xoá chunk khỏi Qdrant
            result = qdrant.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
            )
            print(f"Qdrant delete status: {result.status}")

        # conn.commit() xảy ra ở đây (qua context manager)
    print(f"soft_delete OK — doc_id={doc_id}  collection={collection}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Soft-delete 1 tài liệu")
    parser.add_argument("--doc-id", required=True, help="doc_id cần xoá")
    parser.add_argument(
        "--collection", default="bctc_structural", help="Qdrant collection"
    )
    args = parser.parse_args()
    soft_delete(args.doc_id, args.collection)


if __name__ == "__main__":
    main()
