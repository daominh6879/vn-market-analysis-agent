"""
rag/index.py — Index văn bản đã parse vào Qdrant (idempotent — Bài 9).

Usage:
    python rag/index.py --input outputs/hpg_pymupdf.md --strategy structural --metadata ticker=HPG,year=2025
    python rag/index.py --input outputs/hpg_pymupdf.md --strategy fixed
    python rag/index.py --input outputs/hpg_pymupdf.md --all-strategies --metadata ticker=HPG,year=2025
    python rag/index.py --input outputs/hpg_pymupdf.md --collection my_custom_name --strategy fixed

Collection name auto-built as: {ticker}_{version}_{strategy_short}_{meta|nometa}
  e.g. hpg_b7_structural_meta, hpg_b7_fixed_nometa
Override with --collection.

Idempotent: chạy nhiều lần với cùng file → count và IDs trong Qdrant không đổi.
- doc_id = sha256(content)[:16]
- chunk_id = UUID5(namespace, f"{doc_id}_{i:04d}") — deterministic UUID
- Trước khi upsert: xoá chunk cũ của doc_id này
- Collection chỉ tạo nếu chưa có, không drop
- Bài 10: đăng ký doc vào bảng Postgres `documents` sau khi index
"""
from __future__ import annotations

import argparse
import hashlib
import os
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

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from rag.chunking import chunk_fixed, chunk_structural, chunk_hierarchical, prepend_metadata
from data.db import get_conn

# ── Config from env ───────────────────────────────────────────────────────────

QDRANT_URL        = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL        = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_EMBED     = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
DEFAULT_TICKER    = os.environ.get("TICKER", "hpg").lower()
DEFAULT_VERSION   = os.environ.get("INDEX_VERSION", "b7")

FIXED_SIZE        = int(os.environ.get("CHUNK_FIXED_SIZE", "512"))
FIXED_OVERLAP     = int(os.environ.get("CHUNK_FIXED_OVERLAP", "64"))
STRUCTURAL_MAX    = int(os.environ.get("CHUNK_STRUCTURAL_MAX_SIZE", "800"))

# ── Constants ─────────────────────────────────────────────────────────────────

_CHUNK_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

STRATEGY_SHORT = {
    "fixed":        "fixed",
    "structural":   "structural",
    "hierarchical": "hier",
}


def make_strategies() -> dict:
    return {
        "fixed":        lambda t: chunk_fixed(t, size=FIXED_SIZE, overlap=FIXED_OVERLAP),
        "structural":   lambda t: chunk_structural(t, max_size=STRUCTURAL_MAX),
        "hierarchical": lambda t: [h.child for h in chunk_hierarchical(t)],
    }


def build_collection_name(ticker: str, version: str, strategy: str, has_meta: bool) -> str:
    """Construct canonical collection name from components."""
    short = STRATEGY_SHORT[strategy]
    suffix = "meta" if has_meta else "nometa"
    return f"{ticker.lower()}_{version}_{short}_{suffix}"


# ── Embedding ─────────────────────────────────────────────────────────────────

def _embed_one(text: str, model: str) -> list[float]:
    """Embed single text, trying /api/embed (Ollama >= 0.1.31) then /api/embeddings."""
    for path, payload in (
        ("/api/embed",       {"model": model, "input": text}),
        ("/api/embeddings",  {"model": model, "prompt": text}),
    ):
        r = httpx.post(f"{OLLAMA_URL}{path}", json=payload, timeout=120)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        data = r.json()
        vec = data.get("embedding") or (data.get("embeddings") or [[]])[0]
        if vec:
            return vec
    raise httpx.HTTPError("Ollama embed failed on both /api/embed and /api/embeddings")


def embed_batch(texts: list[str], model: str, batch_size: int = 20) -> list[list[float]]:
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for text in batch:
            vecs.append(_embed_one(text, model))
        print(f"  embedded {min(i + batch_size, len(texts))}/{len(texts)}", end="\r")
    print()
    return vecs


def get_embed_dim(model: str) -> int:
    return len(embed_batch(["test"], model)[0])


# ── Qdrant helpers ────────────────────────────────────────────────────────────

def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"  collection '{name}' created (dim={dim})")
    else:
        print(f"  collection '{name}' exists (dim={dim})")


def recreate_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Drop collection if exists, then create fresh. Used by evals for clean runs."""
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        client.delete_collection(name)
        print(f"  collection '{name}' dropped")
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"  collection '{name}' created (dim={dim})")


def delete_doc_chunks(client: QdrantClient, collection: str, doc_id: str) -> None:
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
            payload={"text": chunks[i], "idx": i, "doc_id": doc_id, **(meta or {})},
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
    strategies = make_strategies()
    chunks = strategies[strategy](text)
    print(f"  doc_id={doc_id}  strategy={strategy}  chunks={len(chunks)}")

    dim = get_embed_dim(embed_model)
    ensure_collection(client, collection, dim)
    delete_doc_chunks(client, collection, doc_id)
    index_chunks(client, collection, chunks, embed_model, doc_id, meta)

    elapsed = time.perf_counter() - t0
    print(f"  done in {elapsed:.1f}s")

    _register_doc(doc_id, source_uri=source_uri or text[:80], collection=collection)
    print()
    return len(chunks)


def _register_doc(doc_id: str, source_uri: str, collection: str) -> None:
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
        raise RuntimeError(f"_register_doc failed for doc_id={doc_id}: {e}") from e


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
    strategies = make_strategies()

    parser = argparse.ArgumentParser(description="Index parsed text into Qdrant (idempotent)")
    parser.add_argument("--input", required=True, help="Path to parsed markdown file")
    parser.add_argument("--collection", help="Override collection name (default: auto-built from ticker/version/strategy/meta)")
    parser.add_argument("--strategy", choices=list(strategies), help="Chunking strategy")
    parser.add_argument("--all-strategies", action="store_true", help="Index all 3 strategies")
    parser.add_argument("--embed", default=DEFAULT_EMBED, help="Ollama embedding model")
    parser.add_argument("--ticker", default=DEFAULT_TICKER, help="Ticker symbol (default: $TICKER env)")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Index version tag (default: $INDEX_VERSION env)")
    parser.add_argument("--metadata", help="Metadata prepended to each chunk, e.g. ticker=HPG,year=2025")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    meta = parse_meta(args.metadata)
    client = QdrantClient(url=QDRANT_URL)
    doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]

    print(f"Input       : {args.input}  ({len(text):,} chars)")
    print(f"doc_id      : {doc_id}")
    print(f"Embed model : {args.embed}")
    print(f"Ticker      : {args.ticker}  version: {args.version}")
    print(f"Metadata    : {meta}\n")

    if args.all_strategies:
        for strategy in strategies:
            collection = args.collection or build_collection_name(args.ticker, args.version, strategy, bool(meta))
            print(f"── {strategy} → {collection} ──")
            run(text, collection, strategy, args.embed, meta, client, doc_id=doc_id, source_uri=args.input)
    else:
        if not args.strategy:
            parser.error("--strategy required unless --all-strategies")
        collection = args.collection or build_collection_name(args.ticker, args.version, args.strategy, bool(meta))
        print(f"── {args.strategy} → {collection} ──")
        run(text, collection, args.strategy, args.embed, meta, client, doc_id=doc_id, source_uri=args.input)


if __name__ == "__main__":
    main()
