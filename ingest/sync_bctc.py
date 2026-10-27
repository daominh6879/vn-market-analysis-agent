"""
ingest/sync_bctc.py — Make sure the latest annual BCTC of each ticker exists in MinIO and Qdrant.

For every ticker:
  1. Ask CafeF for the BCTC file list and pick the latest annual report (Quarter=5).
  2. MinIO  : object `<ticker>-docs/<year>/<filename>` missing  -> download PDF + upload.
  3. Qdrant : no active `documents` row for that key, or no points for its doc_id
              in `bctc_structural`                                -> parse + chunk + embed + upsert.

Scheduled by the Dagster asset `bctc_sync` (pipeline/assets_bctc.py); this CLI is a debugging wrapper around run().

Usage:
    python scripts/sync_bctc.py --dry-run                 # report what is missing, change nothing
    python scripts/sync_bctc.py                           # all active tickers
    python scripts/sync_bctc.py --tickers HPG VCB FPT
    python scripts/sync_bctc.py --tickers HPG --report-type consolidated
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.config import settings

CAFEF_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx"
COLLECTION = "bctc_structural"
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
CHUNK_STRATEGY = os.getenv("CHUNK_STRATEGY", "structural")
ANNUAL_QUARTER = 5  # CafeF marks full-year reports as Quarter=5 ("CN/<year>")
HEADERS = {"User-Agent": "Mozilla/5.0"}


# ── CafeF ─────────────────────────────────────────────────────────────────────

def pick_latest_annual(ticker: str, report_type: str) -> dict | None:
    """Return {"year", "name", "url", "filename"} for the latest annual report, or None."""
    r = httpx.get(
        CAFEF_URL,
        params={"Symbol": ticker.lower(), "Type": 1, "Year": 0},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("Data") or []

    want_consolidated = report_type == "consolidated"
    annual = [
        x for x in items
        if x.get("Quarter") == ANNUAL_QUARTER
        and x.get("Link")
        and ("hợp nhất" in x["Name"].lower()) == want_consolidated
    ]
    if not annual:
        return None
    # newest year first; among the same year prefer the audited report
    annual.sort(key=lambda x: (x["Year"], "kiểm toán" in x["Name"].lower()), reverse=True)
    best = annual[0]
    url = best["Link"]
    filename = unquote(Path(urlparse(url).path).name)
    return {"year": best["Year"], "name": best["Name"], "url": url, "filename": filename}


def download_pdf(url: str) -> bytes:
    r = httpx.get(url, headers=HEADERS, timeout=120, follow_redirects=True)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF (first bytes {r.content[:8]!r}): {url}")
    return r.content


# ── MinIO / Qdrant checks ─────────────────────────────────────────────────────

def minio_client():
    from minio import Minio
    endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    return Minio(
        endpoint.replace("http://", "").replace("https://", ""),
        access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        secure=endpoint.startswith("https://"),
    )


def in_minio(client, bucket: str, key: str) -> bool:
    from minio.error import S3Error
    if not client.bucket_exists(bucket):
        return False
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchObject"):
            return False
        raise


def indexed_doc_id(qdrant, key: str) -> str | None:
    """doc_id of the active documents row for `key` if its points really exist in Qdrant."""
    from core.db import get_conn
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id FROM documents WHERE source_uri = %s AND status = 'active'",
                (key,),
            )
            rows = cur.fetchall()

    if not rows or COLLECTION not in {c.name for c in qdrant.get_collections().collections}:
        return None
    for (doc_id,) in rows:
        n = qdrant.count(
            collection_name=COLLECTION,
            count_filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
            exact=True,
        ).count
        if n > 0:
            return doc_id
    return None


# ── Parse + embed (same steps as the Dagster parsed_doc / embeddings assets) ──

def sector_of(ticker: str) -> str:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sector FROM securities WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
        return row[0].lower().strip() if row and row[0] else "other"
    except Exception:
        return "other"


def parse_and_embed(client, qdrant, ticker: str, bucket: str, key: str, year: int, report_type: str) -> int:
    import hashlib

    from core.parse import parse_with_pymupdf
    from core.quality import assess_quality
    from rag.index import run as index_run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        client.fget_object(bucket, key, tmp_path)
        parsed = parse_with_pymupdf(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    quality = assess_quality(parsed.content, parsed.metadata.get("num_pages", 1))
    if not quality.passed:
        raise RuntimeError(f"quality gate failed for {key}: {quality.reason}")

    doc_id = hashlib.sha256(parsed.content.encode()).hexdigest()[:16]
    md_key = f"parsed/{key[:-4]}.md" if key.lower().endswith(".pdf") else f"parsed/{key}.md"
    md_bytes = parsed.content.encode("utf-8")
    client.put_object(bucket, md_key, io.BytesIO(md_bytes), length=len(md_bytes), content_type="text/markdown")

    n = index_run(
        text=parsed.content,
        collection=COLLECTION,
        strategy=CHUNK_STRATEGY,
        embed_model=EMBED_MODEL,
        meta={
            "ticker": ticker,
            "sector": sector_of(ticker),
            "year": str(year),
            "report_type": report_type,
            "source_key": key,
        },
        client=qdrant,
        doc_id=doc_id,
        source_uri=key,
    )
    if n == 0:
        raise RuntimeError(f"0 chunks produced for {key}")
    return n


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_ticker(client, qdrant, ticker: str, report_type: str, dry: bool) -> str:
    bucket = f"{ticker.lower()}-docs"
    info = pick_latest_annual(ticker, report_type)
    if info is None:
        raise RuntimeError(f"CafeF has no annual {report_type} BCTC for {ticker}")

    key = f"{info['year']}/{info['filename']}"
    has_minio = in_minio(client, bucket, key)
    has_qdrant = indexed_doc_id(qdrant, key) is not None
    print(f"  {info['name']}  ->  {bucket}/{key}")
    print(f"  MinIO: {'ok' if has_minio else 'MISSING'}   Qdrant: {'ok' if has_qdrant else 'MISSING'}")

    if has_minio and has_qdrant:
        return "up-to-date"
    if dry:
        return "would sync"

    if not has_minio:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        data = download_pdf(info["url"])
        client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type="application/pdf")
        print(f"  uploaded {len(data) / 1e6:.1f} MB to MinIO")

    if not has_qdrant:
        n = parse_and_embed(client, qdrant, ticker, bucket, key, info["year"], report_type)
        print(f"  embedded {n} chunks into '{COLLECTION}'")
    return "synced"


def run(tickers: list[str] | None = None, report_type: str = "standalone", dry_run: bool = False) -> dict:
    """Sync every ticker (default: all active). Per-ticker failures are collected, not raised, except when nothing
    succeeded at all (so a dead CafeF/MinIO/Qdrant fails the run). Returns {"results": {ticker: status}, "failed": {...}}."""
    from qdrant_client import QdrantClient

    if not tickers:
        from core.tickers import get_tickers
        tickers = get_tickers()
    tickers = [t.upper() for t in tickers]
    if not tickers:
        raise RuntimeError("no tickers to sync")

    client = minio_client()
    qdrant = QdrantClient(settings.QDRANT_HOST, port=settings.QDRANT_PORT)

    results: dict[str, str] = {}
    failed: dict[str, str] = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] {ticker}")
        try:
            results[ticker] = sync_ticker(client, qdrant, ticker, report_type, dry_run)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed[ticker] = str(exc)
    if not results:
        raise RuntimeError(f"bctc_sync: 0/{len(tickers)} tickers succeeded; failed={dict(list(failed.items())[:5])}")
    return {"results": results, "failed": failed}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252; report names are Vietnamese
    parser = argparse.ArgumentParser(description="Sync latest annual BCTC into MinIO + Qdrant")
    parser.add_argument("--tickers", nargs="*", help="default: all active tickers in securities")
    parser.add_argument("--report-type", choices=["standalone", "consolidated"], default="standalone",
                        help="standalone = công ty mẹ (pipeline default), consolidated = hợp nhất")
    parser.add_argument("--dry-run", action="store_true", help="only report what is missing")
    args = parser.parse_args()

    try:
        out = run(args.tickers, args.report_type, args.dry_run)
    except RuntimeError as exc:
        sys.exit(str(exc))

    print("\n" + "=" * 60)
    for ticker, status in out["results"].items():
        print(f"  {ticker:<8} {status}")
    for ticker, err in out["failed"].items():
        print(f"  {ticker:<8} FAILED: {err}")
    if out["failed"]:
        sys.exit(f"{len(out['failed'])} ticker(s) failed: {', '.join(out['failed'])}")


if __name__ == "__main__":
    main()
