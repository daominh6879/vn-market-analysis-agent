"""
ingest/sync_bctc.py — Make sure the latest annual BCTC of each ticker exists in MinIO and Qdrant.

For every ticker:
  1. Ask CafeF for the BCTC file list and rank the annual reports (Quarter=5): newest year first, then the requested
     report type (standalone/consolidated), then audited. A newer year of the other type beats an older year of the
     requested type, and the report type actually indexed goes into the Qdrant payload.
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
import json
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
# CafeF lists most links on cafefnew.mediacdn.vn, which 404s for some files; the same path is served by these hosts
MIRROR_HOSTS = ("cafef1.mediacdn.vn", "s.cafef.vn")
MAX_CANDIDATES = 3  # older reports are used only when every mirror of the newer one fails


# ── CafeF ─────────────────────────────────────────────────────────────────────

def _report_type_of(name: str) -> str:
    return "consolidated" if "hợp nhất" in name.lower() else "standalone"


def annual_candidates(ticker: str, report_type: str) -> list[dict]:
    """Annual reports on CafeF, best first: [{"year", "name", "url", "filename", "report_type"}, ...] (at most MAX_CANDIDATES)."""
    r = httpx.get(
        CAFEF_URL,
        params={"Symbol": ticker.lower(), "Type": 1, "Year": 0},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    rows = [
        {
            "year": x["Year"],
            "name": x["Name"],
            "url": x["Link"],
            "filename": unquote(Path(urlparse(x["Link"]).path).name),
            "report_type": _report_type_of(x["Name"]),
        }
        for x in (r.json().get("Data") or [])
        if x.get("Quarter") == ANNUAL_QUARTER and x.get("Link")
    ]
    rows.sort(key=lambda x: (x["year"], x["report_type"] == report_type, "kiểm toán" in x["name"].lower()), reverse=True)
    return rows[:MAX_CANDIDATES]


def pick_latest_annual(ticker: str, report_type: str) -> dict | None:
    """The best candidate of annual_candidates(), or None."""
    rows = annual_candidates(ticker, report_type)
    return rows[0] if rows else None


def mirror_urls(url: str) -> list[str]:
    """The listed URL first, then the same path on each mirror host."""
    parts = urlparse(url)
    return [url] + [parts._replace(netloc=h).geturl() for h in MIRROR_HOSTS if h != parts.netloc]


def download_pdf(url: str) -> bytes:
    last: Exception | None = None
    for candidate in mirror_urls(url):
        try:
            r = httpx.get(candidate, headers=HEADERS, timeout=120, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            last = exc
            continue
        if not r.content.startswith(b"%PDF"):
            last = RuntimeError(f"not a PDF (first bytes {r.content[:8]!r}): {candidate}")
            continue
        return r.content
    assert last is not None
    raise last


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


def parsed_key(key: str) -> str:
    """MinIO key of the parsed markdown stored next to a PDF."""
    return f"parsed/{key[:-4]}.md" if key.lower().endswith(".pdf") else f"parsed/{key}.md"


# ── Balance-sheet sanity check (core/balance_check.py) ────────────────────────

def stored_quality(doc_id: str) -> tuple[str | None, int | None]:
    """(documents.quality, version of the check that produced it) — (None, None) when unchecked or the column is missing."""
    from core.db import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT quality, (quality_detail->>'version')::int FROM documents WHERE doc_id = %s", (doc_id,))
                row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)
    except Exception:
        return None, None


def record_quality(qdrant, doc_id: str, check) -> None:
    """Persist a BalanceCheck on the documents row and on every Qdrant chunk of the document (payload `quality`)."""
    import json

    from core.db import get_conn
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    qdrant.set_payload(
        collection_name=COLLECTION,
        payload={"quality": check.status},
        points=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]),
    )
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET quality = %s, quality_detail = %s::jsonb WHERE doc_id = %s",
                    (check.status, json.dumps(check.as_dict(), ensure_ascii=False), doc_id),
                )
    except Exception as exc:   # column missing: migration 044 not applied; the payload flag is still set
        print(f"  [warn] quality not saved to documents (run migration 044?): {exc}")


def ensure_quality(client, qdrant, bucket: str, key: str) -> str:
    """Make sure the indexed document has a balance-sheet check; return a log note ("" when it is fine).

    Runs for freshly indexed documents and, because it only needs the parsed markdown already in MinIO, also fills in
    documents indexed before the check existed or checked by an older version of its rules, without re-OCR. Flags only;
    it never blocks indexing.
    """
    from core.balance_check import CHECK_VERSION, balance_check

    doc_id = indexed_doc_id(qdrant, key)
    if doc_id is None:
        return ""
    status, version = stored_quality(doc_id)
    detail = ""
    if status is None or version != CHECK_VERSION:         # unchecked, or checked by an older version of the rules
        try:
            text = client.get_object(bucket, parsed_key(key)).read().decode("utf-8")
        except Exception:
            return ""                       # no parsed markdown to check
        check = balance_check(text)
        record_quality(qdrant, doc_id, check)
        status, detail = check.status, check.detail
        print(f"  balance check: {status}" + (f" ({detail})" if detail else ""))
    return "" if status == "ok" else f"balance check {status}" + (f": {detail}" if detail else "")


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
    md_key = parsed_key(key)
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

def sync_ticker(client, qdrant, ticker: str, report_type: str, dry: bool) -> tuple[str, str]:
    """Bring the best available annual report into MinIO + Qdrant. Returns (status, note); note is "" for the plain case."""
    bucket = f"{ticker.lower()}-docs"
    candidates = annual_candidates(ticker, report_type)
    if not candidates:
        raise RuntimeError(f"CafeF lists no annual BCTC for {ticker}")

    failures: list[str] = []
    for info in candidates:
        key = f"{info['year']}/{info['filename']}"
        has_minio = in_minio(client, bucket, key)
        has_qdrant = indexed_doc_id(qdrant, key) is not None
        print(f"  {info['name']}  ->  {bucket}/{key}")
        print(f"  MinIO: {'ok' if has_minio else 'MISSING'}   Qdrant: {'ok' if has_qdrant else 'MISSING'}")

        status = "up-to-date" if has_minio and has_qdrant else None
        if status is None and dry:
            status = "would sync"
        if status is None:
            if not has_minio:
                try:
                    data = download_pdf(info["url"])
                except (httpx.HTTPError, RuntimeError) as exc:
                    failures.append(f"FY{info['year']} {info['report_type']}: {exc}")
                    print(f"  download failed on every mirror: {exc}")
                    continue
                if not client.bucket_exists(bucket):
                    client.make_bucket(bucket)
                client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type="application/pdf")
                print(f"  uploaded {len(data) / 1e6:.1f} MB to MinIO")
            if not has_qdrant:
                n = parse_and_embed(client, qdrant, ticker, bucket, key, info["year"], info["report_type"])
                print(f"  embedded {n} chunks into '{COLLECTION}'")
            status = "synced"

        notes = []
        if not dry:
            quality_note = ensure_quality(client, qdrant, bucket, key)
            if quality_note:
                notes.append(quality_note)
        if info is not candidates[0]:
            notes.append(f"FY{info['year']} {info['report_type']} used; newer file unavailable ({'; '.join(failures)})")
        if info["report_type"] != report_type:
            notes.append(f"{info['report_type']} FY{info['year']} (no {report_type} report that year)")
        return status, " | ".join(notes)
    raise RuntimeError("no downloadable annual report: " + "; ".join(failures))


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
    notes: dict[str, str] = {}
    failed: dict[str, str] = {}
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] {ticker}")
        try:
            results[ticker], note = sync_ticker(client, qdrant, ticker, report_type, dry_run)
            if note:
                notes[ticker] = note
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed[ticker] = str(exc)
    if not results:
        raise RuntimeError(f"bctc_sync: 0/{len(tickers)} tickers succeeded; failed={dict(list(failed.items())[:5])}")
    return {"results": results, "failed": failed, "notes": notes}


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
        print(f"  {ticker:<8} {status}" + (f"   [{out['notes'][ticker]}]" if ticker in out["notes"] else ""))
    for ticker, err in out["failed"].items():
        print(f"  {ticker:<8} FAILED: {err}")
    if out["failed"]:
        sys.exit(f"{len(out['failed'])} ticker(s) failed: {', '.join(out['failed'])}")


if __name__ == "__main__":
    main()
