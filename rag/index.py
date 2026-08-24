"""
rag/index.py — Index văn bản đã parse vào Qdrant (idempotent — Bài 9).

Usage:
    python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_fixed_512 --strategy fixed
    python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_structural --strategy structural
    python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_hierarchical --strategy hierarchical
    python rag/index.py --input outputs/hpg_pymupdf.md --all-strategies
    python rag/index.py --input outputs/hpg_pymupdf.md --collection hpg_fixed_512 --strategy fixed --metadata ticker=HPG,year=2025

Idempotent: chạy nhiều lần với cùng file → count và IDs trong Qdrant không đổi.
- doc_id = sha256(content)[:16] — xác định từ nội dung
- chunk_id = UUID5(namespace, f"{doc_id}_{i:04d}") — deterministic UUID
- Trước khi upsert: xoá chunk cũ của doc_id này
- Collection chỉ tạo nếu chưa có, không drop
- Bài 10: đăng ký doc vào bảng Postgres `documents` sau khi index
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import uuid
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from rag.chunking import chunk_fixed, chunk_structural, chunk_hierarchical, prepend_metadata
from data.db import get_conn

QDRANT_URL = "http://localhost:6333"
OLLAMA_URL = "http://localhost:11434"

# Namespace cố định cho UUID5 — đảm bảo determinism giữa các lần chạy
_CHUNK_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

STRATEGIES = {
    "fixed":        lambda t: chunk_fixed(t, size=512, overlap=64),
    "structural":   lambda t: chunk_structural(t, max_size=800),
    "hierarchical": lambda t: [h.child for h in chunk_hierarchical(t)],
}

ALL_STRATEGY_COLLECTIONS = {
    "fixed":        "hpg_fixed_512",
    "structural":   "hpg_structural",
    "hierarchical": "hpg_hierarchical",
}


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_batch(texts: list[str], model: str, batch_size: int = 20) -> list[list[float]]:
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for text in batch:
            r = httpx.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=120,
            )
            r.raise_for_status()
            vecs.append(r.json()["embedding"])
        print(f"  embedded {min(i + batch_size, len(texts))}/{len(texts)}", end="\r")
    print()
    return vecs


def get_embed_dim(model: str) -> int:
    return len(embed_batch(["test"], model)[0])


# ── Qdrant helpers ────────────────────────────────────────────────────────────

def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Tạo collection nếu chưa có. Không drop collection đã tồn tại."""
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"  collection '{name}' created (dim={dim})")
    else:
        print(f"  collection '{name}' exists (dim={dim})")


def delete_doc_chunks(client: QdrantClient, collection: str, doc_id: str) -> None:
    """Xoá tất cả chunk của doc_id này trước khi upsert lại."""
    try:
        client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
    except Exception:
        pass


def index_chunks(
    client: QdrantClient,
    collection: str,
    chunks: list[str],
    embed_model: str,
    doc_id: str,
    meta: dict | None = None,
) -> None:
    texts = [prepend_metadata(c, meta) if meta else c for c in chunks]
    vecs = embed_batch(texts, embed_model)
    points = [
        PointStruct(
            id=uuid.uuid5(_CHUNK_NS, f"{doc_id}_{i:04d}"),
            vector=vecs[i],
            payload={"text": chunks[i], "idx": i, "doc_id": doc_id},
        )
        for i in range(len(chunks))
    ]
    client.upsert(collection_name=collection, points=points)
    print(f"  upserted {len(points)} vectors → '{collection}'")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    text: str,
    collection: str,
    strategy: str,
    embed_model: str,
    meta: dict | None,
    client: QdrantClient,
    doc_id: str | None = None,
    source_uri: str = "",
) -> int:
    if doc_id is None:
        doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]

    t0 = time.perf_counter()
    chunker = STRATEGIES[strategy]
    chunks = chunker(text)
    print(f"  doc_id={doc_id}  strategy={strategy}  chunks={len(chunks)}")

    dim = get_embed_dim(embed_model)
    ensure_collection(client, collection, dim)
    delete_doc_chunks(client, collection, doc_id)
    index_chunks(client, collection, chunks, embed_model, doc_id, meta)

    elapsed = time.perf_counter() - t0
    print(f"  done in {elapsed:.1f}s")

    # Bài 10: đăng ký vào Postgres documents (upsert — idempotent)
    _register_doc(doc_id, source_uri=source_uri or text[:80], collection=collection)
    print()
    return len(chunks)


def _register_doc(doc_id: str, source_uri: str, collection: str) -> None:
    """Upsert vào bảng documents. Nếu đã có → cập nhật indexed_at, reset status='active'."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (doc_id, status, source_uri, collection, indexed_at)
                    VALUES (%s, 'active', %s, %s, NOW())
                    ON CONFLICT (doc_id) DO UPDATE
                        SET status='active', source_uri=EXCLUDED.source_uri,
                            collection=EXCLUDED.collection, indexed_at=NOW(), deleted_at=NULL
                    """,
                    (doc_id, source_uri, collection),
                )
        print(f"  registered in documents: doc_id={doc_id}")
    except Exception as e:
        print(f"  [warn] không ghi được vào Postgres (chạy migration chưa?): {e}")


def parse_meta(raw: str | None) -> dict | None:
    if not raw:
        return None
    result = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Index parsed text into Qdrant (idempotent)")
    parser.add_argument("--input", required=True, help="Path to parsed markdown file")
    parser.add_argument("--collection", help="Qdrant collection name")
    parser.add_argument(
        "--strategy", choices=list(STRATEGIES), help="Chunking strategy"
    )
    parser.add_argument(
        "--all-strategies", action="store_true",
        help="Index all 3 strategies into their default collection names"
    )
    parser.add_argument(
        "--embed", default="bge-m3", help="Ollama embedding model"
    )
    parser.add_argument(
        "--metadata",
        help="Metadata to prepend to each chunk, e.g. ticker=HPG,year=2025",
    )
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    meta = parse_meta(args.metadata)
    client = QdrantClient("localhost", port=6333)
    doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]

    print(f"Input       : {args.input}  ({len(text):,} chars)")
    print(f"doc_id      : {doc_id}")
    print(f"Embed model : {args.embed}")
    print(f"Metadata    : {meta}\n")

    if args.all_strategies:
        for strategy, collection in ALL_STRATEGY_COLLECTIONS.items():
            print(f"── {strategy} → {collection} ──")
            run(text, collection, strategy, args.embed, meta, client, doc_id=doc_id, source_uri=args.input)
    else:
        if not args.collection or not args.strategy:
            parser.error("--collection and --strategy required unless --all-strategies")
        run(text, args.collection, args.strategy, args.embed, meta, client, doc_id=doc_id, source_uri=args.input)


if __name__ == "__main__":
    main()
