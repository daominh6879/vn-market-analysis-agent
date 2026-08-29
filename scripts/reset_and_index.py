"""
scripts/reset_and_index.py — Xóa toàn bộ Qdrant + Postgres, index lại 2024 + 2025 HPG.

**WARNING: DESTRUCTIVE**
  - Xóa TẤT CẢ collections trong Qdrant (không chỉ hpg_*)
  - TRUNCATE documents, quarantine_log, financial_facts trong Postgres
  - Parse lại cả 2 PDF từ reports/HGP/
  - Index vào hpg_structural (structural, no meta, bge-m3)

Usage:
    uv run python scripts/reset_and_index.py
    uv run python scripts/reset_and_index.py --dry-run   # chỉ print plan, không làm gì
    uv run python scripts/reset_and_index.py --skip-parse  # nếu outputs/ đã có sẵn
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

PDF_2024 = ROOT / "reports" / "HGP" / "2024" / "0004773662551440329ctcp-tp-on-ha-pht29032025-000000bo-co-ti-chnh-m-nm.pdf"
PDF_2025 = ROOT / "reports" / "HGP" / "2025" / "20260327_-_HPG_-_BCTC_Cong_ty_me_sau_kiem_toan_nam_2025_1774869041.pdf"

OUT_2024 = ROOT / "outputs" / "2024" / "hpg_pymupdf.md"
OUT_2025 = ROOT / "outputs" / "2025" / "hpg_pymupdf.md"

# Full matrix: collection → (strategy, embed_model, metadata_str|None)
# metadata_str format: "ticker=HPG,year=multi" — passed to --metadata flag
INDEX_CONFIGS = [
    # label                         collection                   strategy       embed_model            meta
    ("fixed_512|no-meta|bge-m3",    "hpg_b7_fixed_nometa",       "fixed",       "bge-m3",              None),
    ("structural|no-meta|bge-m3",   "hpg_b7_structural_nometa",  "structural",  "bge-m3",              None),
    ("hierarch|no-meta|bge-m3",     "hpg_b7_hier_nometa",        "hierarchical","bge-m3",              None),
    ("fixed_512|meta|bge-m3",       "hpg_b7_fixed_meta",         "fixed",       "bge-m3",              "year={year}"),
    ("structural|meta|bge-m3",      "hpg_b7_structural_meta",    "structural",  "bge-m3",              "year={year}"),
    ("hierarch|meta|bge-m3",        "hpg_b7_hier_meta",          "hierarchical","bge-m3",              "year={year}"),
]


# ── Step 1: Reset Qdrant ──────────────────────────────────────────────────────

def reset_qdrant(dry: bool) -> None:
    from qdrant_client import QdrantClient
    client = QdrantClient("localhost", port=6333)

    cols = [c.name for c in client.get_collections().collections]
    if not cols:
        print("  Qdrant: no collections found — nothing to delete")
        return

    print(f"  Qdrant collections to delete ({len(cols)}):")
    for name in cols:
        info = client.get_collection(name)
        n = info.points_count
        print(f"    {name}  ({n} points)")

    if dry:
        print("  [DRY RUN] skip delete")
        return

    for name in cols:
        client.delete_collection(name)
        print(f"  deleted: {name}")

    print(f"  Qdrant: deleted {len(cols)} collections")


# ── Step 2: Reset Postgres ────────────────────────────────────────────────────

def reset_postgres(dry: bool) -> None:
    from data.db import get_conn

    tables = ["quarantine_log", "documents"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = %s)",
                    (table,),
                )
                exists = cur.fetchone()[0]
                if not exists:
                    print(f"  Postgres: table '{table}' does not exist — skip")
                    continue

                cur.execute(f"SELECT COUNT(*) FROM {table}")
                n = cur.fetchone()[0]
                print(f"  Postgres: TRUNCATE {table}  ({n} rows)")

                if not dry:
                    cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")

        if dry:
            print("  [DRY RUN] skip truncate")


# ── Step 3: Parse PDFs ────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path, out_path: Path, dry: bool) -> None:
    print(f"\n  Parse: {pdf_path.name}")
    print(f"      → {out_path}")

    if not pdf_path.exists():
        print(f"  ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    if dry:
        print("  [DRY RUN] skip parse")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)

    import subprocess
    cmd = [
        sys.executable, str(ROOT / "core" / "parse.py"),
        str(pdf_path),
        "--tool", "pymupdf",
        "--output-dir", str(out_path.parent),
    ]
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.perf_counter() - t0

    if result.returncode != 0:
        print(f"  ERROR: parse.py exited {result.returncode}")
        sys.exit(1)
    if not out_path.exists():
        print(f"  ERROR: expected output not found: {out_path}")
        sys.exit(1)
    print(f"  OK: {out_path.stat().st_size:,} bytes  {elapsed:.0f}s")


# ── Step 4: Index ─────────────────────────────────────────────────────────────

def index_file(
    md_path: Path,
    collection: str,
    strategy: str,
    embed_model: str,
    meta: str | None,
    dry: bool,
) -> None:
    meta_str = f"  meta={meta}" if meta else ""
    print(f"\n  Index: {md_path.name}  → {collection}  [{strategy}|{embed_model}{meta_str}]")

    if dry:
        print("  [DRY RUN] skip index")
        return

    if not md_path.exists():
        print(f"  ERROR: parsed file not found: {md_path}")
        sys.exit(1)

    import subprocess
    cmd = [
        sys.executable, str(ROOT / "rag" / "index.py"),
        "--input", str(md_path),
        "--collection", collection,
        "--strategy", strategy,
        "--embed", embed_model,
    ]
    if meta:
        cmd += ["--metadata", meta]

    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.perf_counter() - t0

    if result.returncode != 0:
        print(f"  ERROR: index.py exited {result.returncode}")
        sys.exit(1)
    print(f"  Done in {elapsed:.0f}s")


# ── Step 5: Verify ────────────────────────────────────────────────────────────

def verify() -> None:
    from qdrant_client import QdrantClient
    from data.db import get_conn

    client = QdrantClient("localhost", port=6333)
    existing = {c.name: c for c in client.get_collections().collections}

    print(f"\n  Qdrant collections ({len(existing)}):")
    for _, collection, strategy, embed_model, meta in INDEX_CONFIGS:
        info = existing.get(collection)
        if info:
            col_info = client.get_collection(collection)
            print(f"    {collection:<35} {col_info.points_count:>5} points")
        else:
            print(f"    {collection:<35} MISSING")

    print(f"\n  Postgres documents ({sum(1 for _ in INDEX_CONFIGS)} configs, 2 files = expect up to {len(INDEX_CONFIGS)*2} rows):")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_id, collection, status FROM documents ORDER BY indexed_at")
            rows = cur.fetchall()
            for row in rows:
                print(f"    {row[0]}  {row[2]}  → {row[1]}")
            print(f"  Total: {len(rows)} rows")


# ── Main ──────────────────────────────────────────────────────────────────────

class Tee(io.TextIOBase):
    """Write to both stdout and a log file simultaneously."""
    def __init__(self, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_path.open("w", encoding="utf-8")
        self._stdout = sys.__stdout__

    def write(self, s: str) -> int:
        self._log.write(s)
        self._log.flush()
        return self._stdout.write(s)

    def flush(self) -> None:
        self._log.flush()
        self._stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only, do nothing")
    parser.add_argument("--skip-parse", action="store_true",
                        help="Skip PDF parsing — use existing outputs/*.md files")
    parser.add_argument("--log", default="outputs/reset_index.log",
                        help="Log file path (default: outputs/reset_index.log)")
    args = parser.parse_args()

    dry = args.dry_run

    log_path = ROOT / args.log
    sys.stdout = Tee(log_path)
    print(f"Log: {log_path}")

    print("=" * 60)
    print("RESET + REINDEX — HPG 2024 + 2025")
    if dry:
        print("*** DRY RUN — no changes will be made ***")
    print("=" * 60)

    print(f"\nFiles:")
    print(f"  2024 PDF : {PDF_2024}")
    print(f"  2025 PDF : {PDF_2025}")
    print(f"  2024 out : {OUT_2024}")
    print(f"  2025 out : {OUT_2025}")
    print(f"  configs   : {len(INDEX_CONFIGS)} × 2 files = {len(INDEX_CONFIGS)*2} index runs")

    print(f"\n[1/5] Reset Qdrant")
    reset_qdrant(dry)

    print(f"\n[2/5] Reset Postgres")
    reset_postgres(dry)

    if not args.skip_parse:
        print(f"\n[3/5] Parse PDFs (pymupdf4llm, vie+eng, OCR)")
        parse_pdf(PDF_2024, OUT_2024, dry)
        parse_pdf(PDF_2025, OUT_2025, dry)
    else:
        print(f"\n[3/5] Parse PDFs — SKIPPED (--skip-parse)")
        if not dry:
            for p in [OUT_2024, OUT_2025]:
                if not p.exists():
                    print(f"  ERROR: {p} not found — run without --skip-parse first")
                    sys.exit(1)
                print(f"  Using existing: {p}  ({p.stat().st_size:,} bytes)")

    n_configs = len(INDEX_CONFIGS)
    n_files = 2
    print(f"\n[4/5] Index into Qdrant  ({n_configs} configs × {n_files} files = {n_configs*n_files} runs)")
    file_years = [(OUT_2024, "2024"), (OUT_2025, "2025")]
    for label, collection, strategy, embed_model, meta_template in INDEX_CONFIGS:
        print(f"\n  --- {label} ---")
        for md_path, year in file_years:
            meta = meta_template.replace("{year}", year) if meta_template else None
            index_file(md_path, collection, strategy, embed_model, meta, dry)

    print(f"\n[5/5] Verify")
    if not dry:
        verify()
    else:
        print("  [DRY RUN] skip verify")

    print("\n" + "=" * 60)
    print("DONE" if not dry else "DRY RUN COMPLETE — run without --dry-run to execute")
    print("=" * 60)


if __name__ == "__main__":
    main()
