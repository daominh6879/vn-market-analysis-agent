"""
scripts/migrate.py — First-run full setup: schema + data population + audit.

Run ONCE on a fresh environment after `docker compose up -d`.

Steps:
  1. SQL migrations   — all infra/migrations/*.sql in dependency order
  2. Seed securities  — ~400 HOSE tickers into securities table
  3. MinIO setup      — create bucket 'bctc-reports' + upload BCTC PDFs
  4. Financial facts  — vnstock (primary) for HPG, VCB, FPT (2020-2025)
  5. OHLCV + foreign  — Fireant (primary) -> VCI/KBS fallback, 1-year backfill
  6. Market index     — SSI iBoard, 365 days (VNINDEX/HNX/UPCOM/VN30/HNX30)
  7. Audit            — scripts/audit_db.py to verify completeness

Usage:
    python scripts/migrate.py
    python scripts/migrate.py --dry-run            # print plan, no changes
    python scripts/migrate.py --skip-minio         # skip MinIO/PDF step
    python scripts/migrate.py --skip-market-data   # skip steps 4-6 (schema only)
    python scripts/migrate.py --tickers HPG,VCB    # limit data fetch to these tickers
    python scripts/migrate.py --skip-audit         # skip final audit
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYTHON = sys.executable
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

MIGRATIONS_DIR = ROOT / "infra" / "migrations"
REPORTS_DIR    = ROOT / "reports"
MINIO_BUCKET   = "bctc-reports"

# Dependency order: FK targets before FK sources, ALTER TABLE last
MIGRATION_ORDER = [
    "001_documents.sql",
    "002_quarantine_log.sql",
    "003_financial_facts.sql",
    "005_news_articles.sql",
    "006_ohlcv_daily.sql",
    "007_market_index_daily.sql",
    "008_securities.sql",
    "009_foreign_flows.sql",
    "009b_financial_facts_idx.sql",
    "010_market_quotes.sql",
    "011_corporate_events.sql",
    "012_broker_views.sql",
    "027_agent_sessions.sql",
    "028_conversations.sql",
    "029_episodic.sql",
    "030_pending_context.sql",
    "004_readonly_role.sql",  # last: GRANTs need tables to exist
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], label: str, dry: bool) -> bool:
    """Run subprocess, print header + return True on success."""
    print(f"\n  >>> {label}")
    if dry:
        print(f"      [DRY] would run: {' '.join(cmd)}")
        return True
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"      FAILED (exit {result.returncode})")
        return False
    return True


# ── Step 1: SQL migrations ────────────────────────────────────────────────────

def run_migrations(dry: bool) -> bool:
    print("\n[1/7] SQL migrations")
    from data.db import get_conn

    ok = err = skip = 0
    ordered_names = list(MIGRATION_ORDER)

    # Run known files in explicit order
    for filename in ordered_names:
        path = MIGRATIONS_DIR / filename
        if not path.exists():
            print(f"  SKIP (not found): {filename}")
            skip += 1
            continue
        print(f"  {filename}", end="", flush=True)
        if dry:
            print("  [DRY]")
            continue
        try:
            sql = path.read_text(encoding="utf-8")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
            print("  OK")
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            err += 1

    # Any extra files not in the explicit list (future migrations)
    seen = set(ordered_names)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in seen:
            continue
        print(f"  {path.name} (extra)", end="", flush=True)
        if dry:
            print("  [DRY]")
            continue
        try:
            sql = path.read_text(encoding="utf-8")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
            print("  OK")
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            err += 1

    if not dry:
        print(f"  -> {ok} OK, {err} errors, {skip} skipped")
        if err:
            print("  WARNING: some migrations failed -- check above")
    return err == 0


# ── Step 2: Seed securities ───────────────────────────────────────────────────

def seed_securities(dry: bool) -> bool:
    print("\n[2/7] Seed securities (~400 HOSE tickers)")
    if dry:
        print("  [DRY] skip")
        return True
    try:
        from ingest.seed_securities import seed_from_hose_seed
        n = seed_from_hose_seed()
        print(f"  -> {n} securities upserted")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Fallback: python ingest/seed_securities.py")
        return False


# ── Step 3: MinIO bucket + PDF upload ────────────────────────────────────────

def setup_minio_and_upload(dry: bool) -> bool:
    print("\n[3/7] MinIO bucket + BCTC PDF upload")
    pdfs = sorted(REPORTS_DIR.rglob("*.pdf"))
    print(f"  PDFs found: {len(pdfs)}")
    for p in pdfs:
        print(f"    {p.relative_to(REPORTS_DIR)}")

    if dry:
        print(f"  [DRY] would create bucket '{MINIO_BUCKET}' and upload {len(pdfs)} PDFs")
        return True

    try:
        from minio import Minio
        from core.config import settings
        client = Minio(
            "localhost:9000",
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=False,
        )
        if client.bucket_exists(MINIO_BUCKET):
            print(f"  bucket '{MINIO_BUCKET}' already exists")
        else:
            client.make_bucket(MINIO_BUCKET)
            print(f"  created bucket '{MINIO_BUCKET}'")
    except Exception as e:
        print(f"  MinIO connection ERROR: {e}")
        print("  Is MinIO running? docker compose up -d minio")
        return False

    ok = err = 0
    for p in pdfs:
        object_name = p.relative_to(REPORTS_DIR).as_posix()
        print(f"  upload {object_name}", end="", flush=True)
        try:
            client.fput_object(MINIO_BUCKET, object_name, str(p), content_type="application/pdf")
            print("  OK")
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            err += 1

    print(f"  -> {ok} uploaded, {err} errors")
    return err == 0


# ── Step 4: Financial facts (vnstock primary) ─────────────────────────────────
#
# populate_financial_data.py already handles:
#   vnstock Finance API -> financial_facts (source='vnstock')
# The table also accepts PDF-extracted facts (source='pdf') but that's done
# separately via ingest/extract_facts.py after PDFs are indexed.

def populate_financials(dry: bool) -> bool:
    # No --tickers arg: populate_financial_data.py calls core.tickers.get_tickers()
    # which reads securities table (populated in step 2). Falls back to TICKERS env var.
    print("\n[4/7] Financial facts (vnstock -> financial_facts)")
    print("  tickers: from securities table (step 2)  periods: 2020-2025")
    return _run(
        [
            PYTHON, "scripts/populate_financial_data.py",
            "--from", "2020",
            "--to", "2025",
        ],
        "vnstock financial_facts + stock_prices (all active tickers)",
        dry,
    )


# ── Step 5: OHLCV + foreign flows (Fireant -> VCI/KBS fallback) ──────────────
#
# fetch_ohlcv.py --migrate does:
#   FireantProvider (primary, includes buyForeignQuantity/sellForeignQuantity)
#   -> FallbackProvider(KbsProvider, VciDirectProvider) if Fireant fails
#   Upserts ohlcv_daily AND foreign_flows in one pass.

def backfill_ohlcv(dry: bool) -> bool:
    print("\n[5/7] OHLCV + foreign flows (Fireant->VCI/KBS, 1-year backfill)")
    print("  Provider chain: Fireant (primary) -> KBS -> VCI")
    print("  Fireant response includes foreign buy/sell -> populates foreign_flows too")
    return _run(
        [PYTHON, "ingest/fetch_ohlcv.py", "--migrate"],
        "OHLCV + foreign_flows backfill (all active securities)",
        dry,
    )


# ── Step 6: Market index (SSI iBoard) ────────────────────────────────────────

def backfill_market_index(dry: bool) -> bool:
    print("\n[6/7] Market index daily (SSI iBoard, 365 days)")
    print("  Indices: VNINDEX, HNX, UPCOM, VN30, HNX30")
    return _run(
        [PYTHON, "ingest/fetch_index.py", "--days", "365"],
        "market_index_daily (SSI)",
        dry,
    )


# ── Step 7: Audit ─────────────────────────────────────────────────────────────

def run_audit(dry: bool) -> bool:
    print("\n[7/7] DB completeness audit")
    if dry:
        print("  [DRY] skip")
        return True
    return _run(
        [PYTHON, "scripts/audit_db.py"],
        "audit_db (all active tickers)",
        dry=False,  # always actually run audit
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="First-run full setup: schema + data + audit")
    parser.add_argument("--dry-run",          action="store_true",
                        help="Print plan, do nothing")
    parser.add_argument("--skip-minio",       action="store_true",
                        help="Skip MinIO bucket creation and PDF upload")
    parser.add_argument("--skip-securities",  action="store_true",
                        help="Skip HOSE securities seed")
    parser.add_argument("--skip-market-data", action="store_true",
                        help="Skip steps 4-6 (financial facts, OHLCV, market index)")
    parser.add_argument("--skip-audit",       action="store_true",
                        help="Skip final DB audit")
    args = parser.parse_args()

    dry = args.dry_run
    print("=" * 60)
    print("MIGRATE -- first-run full setup")
    if dry:
        print("*** DRY RUN -- no changes will be made ***")
    print("Tickers: securities table (seeded in step 2) -> TICKERS env fallback")
    print("=" * 60)

    results: dict[str, bool] = {}

    # Schema
    results["migrations"] = run_migrations(dry)

    if not args.skip_securities:
        results["securities"] = seed_securities(dry)
    else:
        print("\n[2/7] Securities seed -- SKIPPED")

    if not args.skip_minio:
        results["minio"] = setup_minio_and_upload(dry)
    else:
        print("\n[3/7] MinIO/PDF -- SKIPPED")

    # Market data population
    if not args.skip_market_data:
        results["financials"]    = populate_financials(dry)
        results["ohlcv"]         = backfill_ohlcv(dry)
        results["market_index"]  = backfill_market_index(dry)
    else:
        print("\n[4/7] Financial facts -- SKIPPED")
        print("\n[5/7] OHLCV + foreign flows -- SKIPPED")
        print("\n[6/7] Market index -- SKIPPED")

    # Audit
    if not args.skip_audit:
        results["audit"] = run_audit(dry)
    else:
        print("\n[7/7] Audit -- SKIPPED")

    # Summary
    print("\n" + "=" * 60)
    if dry:
        print("DRY RUN COMPLETE -- re-run without --dry-run to execute")
    else:
        print("RESULTS:")
        all_ok = True
        for step, ok in results.items():
            mark = "OK  " if ok else "FAIL"
            print(f"  [{mark}] {step}")
            if not ok:
                all_ok = False

        print()
        if all_ok:
            print("MIGRATION COMPLETE -- all steps passed")
        else:
            print("MIGRATION DONE WITH ERRORS -- check failures above")

        print("\nNext steps:")
        print("  Index BCTC PDFs:      python scripts/reset_and_index.py")
        print("  Start API:            uvicorn api.main:app --reload --port 8031")
        print("  Start UI (streamlit): streamlit run ui/chat.py")
        print("  Start Dagster:        dagster dev -f pipeline/assets.py")
        print("  Daily refresh:        python scripts/daily_ingest.py")
    print("=" * 60)

    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
