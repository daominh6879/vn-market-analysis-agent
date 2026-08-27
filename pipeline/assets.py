"""
pipeline/assets.py — Bài 13: Dagster pipeline cho ingestion HPG PDF.

Flow:
    raw_pdf → parsed_doc → embeddings (Qdrant)
                         → financial_facts (Postgres)

Dagster truyền output giữa assets qua tham số cùng tên.
raw_pdf → list[dict] → parsed_doc nhận làm tham số → trả list[dict] → downstream nhận.
"""
import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    Config,
    Definitions,
    RunConfig,
    ScheduleDefinition,
    SensorResult,
    asset,
    define_asset_job,
    sensor,
)
from dagster import RetryPolicy

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")


# ── Config ────────────────────────────────────────────────────────────────────

class IngestionConfig(Config):
    mode: str = "incremental"         # "incremental" | "full_rebuild"
    ticker: str = "HPG"
    period: str = "2024"
    report_type: str = "standalone"
    object_key: str = ""              # nếu có → chỉ xử lý file này
    embed_model: str = os.getenv("EMBED_MODEL", "bge-m3")
    chunk_strategy: str = os.getenv("CHUNK_STRATEGY", "structural")


def _bucket(cfg: IngestionConfig) -> str:
    """Mỗi ticker có bucket riêng: hpg-docs, vcb-docs, ..."""
    return f"{cfg.ticker.lower()}-docs"


def _collection(cfg: IngestionConfig) -> str:
    """Mỗi ticker có collection riêng: hpg_structural, vcb_structural, ..."""
    return f"{cfg.ticker.lower()}_{cfg.chunk_strategy}"


# ── MinIO client helper ───────────────────────────────────────────────────────

def _delete_old_chunks_by_uri(
    qdrant,
    collection: str,
    source_uri: str,
    new_doc_id: str,
    context: AssetExecutionContext,
) -> None:
    """
    Khi file update (cùng source_uri, doc_id khác), xóa chunk cũ khỏi Qdrant.
    doc_id mới chưa tồn tại nên delete_doc_chunks trong index_run không bắt được.
    """
    try:
        from core.db import get_conn
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT doc_id FROM documents WHERE source_uri = %s AND status = 'active'",
                    (source_uri,),
                )
                rows = cur.fetchall()

        for (old_doc_id,) in rows:
            if old_doc_id != new_doc_id:
                qdrant.delete(
                    collection_name=collection,
                    points_selector=Filter(
                        must=[FieldCondition(key="doc_id", match=MatchValue(value=old_doc_id))]
                    ),
                )
                context.log.info(f"  Xóa chunk cũ: source_uri={source_uri} old_doc_id={old_doc_id}")
    except Exception as exc:
        context.log.warning(f"  Không xóa được chunk cũ (bỏ qua): {exc}")


def _minio():
    from minio import Minio
    url = MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
    secure = MINIO_ENDPOINT.startswith("https://")
    return Minio(
        url,
        access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        secure=secure,
    )


# ── Asset 1: raw_pdf ──────────────────────────────────────────────────────────

@asset(
    group_name="ingestion",
    description="Quét MinIO bucket, trả danh sách object key PDF cần xử lý.",
)
def raw_pdf(context: AssetExecutionContext, config: IngestionConfig) -> list[dict]:
    """Trả list[{"key": str, "etag": str, "size": int}]."""
    client = _minio()

    if not client.bucket_exists(_bucket(config)):
        context.log.warning(f"Bucket '{_bucket(config)}' chưa tồn tại — tạo trống.")
        client.make_bucket(_bucket(config))
        return []

    if config.object_key:
        objects = [{"key": config.object_key, "etag": "", "size": 0}]
    else:
        objects = [
            {"key": obj.object_name, "etag": obj.etag or "", "size": obj.size or 0}
            for obj in client.list_objects(_bucket(config), recursive=True)
            if obj.object_name and obj.object_name.lower().endswith(".pdf")
        ]

    if config.mode == "incremental":
        objects = _filter_unindexed(objects, context)

    context.log.info(f"raw_pdf: {len(objects)} PDF (mode={config.mode})")
    return objects


def _filter_unindexed(objects: list[dict], context: AssetExecutionContext) -> list[dict]:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT source_uri FROM documents WHERE status = 'active'")
                indexed = {row[0] for row in cur.fetchall()}
        fresh = [o for o in objects if o["key"] not in indexed]
        context.log.info(f"incremental: {len(objects)} total, {len(indexed)} indexed, {len(fresh)} mới")
        return fresh
    except Exception as exc:
        context.log.warning(f"Không đọc documents table, xử lý tất cả: {exc}")
        return objects


# ── Asset 2: parsed_doc ───────────────────────────────────────────────────────

@asset(
    group_name="ingestion",
    description="Download PDF từ MinIO → parse → quality gate.",
    retry_policy=RetryPolicy(max_retries=2, delay=15),
)
def parsed_doc(
    context: AssetExecutionContext,
    config: IngestionConfig,
    raw_pdf: list[dict],          # nhận output từ asset raw_pdf
) -> list[dict]:
    """
    Trả list[{"key", "doc_id", "markdown", "num_pages", "dagster_run_id"}].
    File không qua quality gate → log warning, bỏ qua.
    """
    from core.parse import parse_with_pymupdf
    from core.quality import assess_quality

    client = _minio()
    results: list[dict] = []

    for obj in raw_pdf:
        key = obj["key"]
        context.log.info(f"Parsing: {key}")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            client.fget_object(_bucket(config), key, tmp_path)
            parsed = parse_with_pymupdf(tmp_path)
            doc_id = hashlib.sha256(parsed.content.encode()).hexdigest()[:16]

            quality = assess_quality(parsed.content, parsed.metadata.get("num_pages", 1))
            if not quality.passed:
                context.log.warning(f"Quality FAIL: {key} — {quality.reason}")
                continue

            md_key = f"parsed/{key[:-4]}.md" if key.lower().endswith(".pdf") else f"parsed/{key}.md"
            md_bytes = parsed.content.encode("utf-8")
            client.put_object(
                _bucket(config),
                md_key,
                io.BytesIO(md_bytes),
                length=len(md_bytes),
                content_type="text/markdown",
            )
            context.log.info(f"Saved markdown: {md_key}")

            results.append({
                "key": key,
                "doc_id": doc_id,
                "markdown": parsed.content,
                "num_pages": parsed.metadata.get("num_pages", 0),
                "dagster_run_id": context.run_id,
                "md_key": md_key,
            })
            context.log.info(f"OK: {key} (doc_id={doc_id}, {len(parsed.content):,} chars)")

        except Exception as exc:
            context.log.error(f"Parse FAIL: {key} — {exc}")
            raise
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    context.log.info(f"parsed_doc: {len(results)}/{len(raw_pdf)} qua quality gate")
    return results


# ── Asset 3a: embeddings ──────────────────────────────────────────────────────

@asset(
    group_name="ingestion",
    description="Chunk → embed → upsert Qdrant. Tuần tự — không song song (tránh quá tải Ollama).",
    retry_policy=RetryPolicy(max_retries=3, delay=30),
)
def embeddings(
    context: AssetExecutionContext,
    config: IngestionConfig,
    parsed_doc: list[dict],       # nhận output từ asset parsed_doc
) -> dict:
    """Trả {"indexed": int, "total_chunks": int}."""
    from rag.index import run as index_run
    from qdrant_client import QdrantClient

    qdrant = QdrantClient("localhost", port=6333)
    total_indexed = 0
    total_chunks = 0

    for doc in parsed_doc:
        context.log.info(f"Indexing: {doc['key']} (doc_id={doc['doc_id']})")

        # Nếu cùng source_uri đã được index với doc_id khác (file update),
        # xóa chunk cũ trước để tránh tích lũy.
        _delete_old_chunks_by_uri(qdrant, _collection(config), doc["key"], doc["doc_id"], context)

        meta = {
            "ticker": config.ticker,
            "year": config.period,
            "report_type": config.report_type,
            "source_key": doc["key"],
            "dagster_run_id": doc["dagster_run_id"],
        }
        n = index_run(
            text=doc["markdown"],
            collection=_collection(config),
            strategy=config.chunk_strategy,
            embed_model=config.embed_model,
            meta=meta,
            client=qdrant,
            doc_id=doc["doc_id"],
            source_uri=doc["key"],
        )
        total_chunks += n
        total_indexed += 1
        context.log.info(f"  → {n} chunks")

    context.log.info(f"embeddings: {total_indexed} docs, {total_chunks} chunks")
    return {"indexed": total_indexed, "total_chunks": total_chunks}


# ── Asset 3b: financial_facts ─────────────────────────────────────────────────

@asset(
    group_name="ingestion",
    description="LLM extract số liệu tài chính → validate → Postgres. Song song với embeddings.",
    retry_policy=RetryPolicy(max_retries=2, delay=20),
)
def financial_facts(
    context: AssetExecutionContext,
    config: IngestionConfig,
    parsed_doc: list[dict],       # nhận output từ asset parsed_doc
) -> dict:
    """Trả {"extracted": int, "total_facts": int}."""
    from ingest.extract_facts import extract_facts_from_markdown, validate_facts, insert_facts

    total_extracted = 0
    total_facts = 0

    for doc in parsed_doc:
        context.log.info(f"Extracting facts: {doc['key']}")
        try:
            facts = extract_facts_from_markdown(
                markdown=doc["markdown"],
                ticker=config.ticker,
                period=config.period,
                report_type=config.report_type,
                source_file=doc["key"],
            )
            errors = validate_facts(facts)
            for e in errors:
                context.log.warning(f"  [WARN] {e.type}: {e.message}")

            n = insert_facts(facts)
            total_facts += n
            total_extracted += 1
            context.log.info(f"  → {n} facts")
        except Exception as exc:
            context.log.error(f"Extract FAIL: {doc['key']} — {exc}")
            raise

    context.log.info(f"financial_facts: {total_extracted} docs, {total_facts} facts")
    return {"extracted": total_extracted, "total_facts": total_facts}


# ── Config xóa ───────────────────────────────────────────────────────────────

class DeleteConfig(Config):
    ticker: str = "HPG"
    object_key: str = ""


# ── Asset: delete_doc ─────────────────────────────────────────────────────────

@asset(
    group_name="ingestion",
    description="Soft-delete trong Postgres + xóa chunk khỏi Qdrant khi PDF bị xóa khỏi MinIO.",
)
def delete_doc(context: AssetExecutionContext, config: DeleteConfig) -> dict:
    """
    1. Tìm doc_id trong Postgres documents theo source_uri = object_key
    2. Xóa chunk khỏi Qdrant (theo doc_id)
    3. Soft-delete trong Postgres (status='deleted')
    Trả {"deleted_docs": int, "deleted_chunks": int}.
    """
    from core.db import get_conn
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    cfg = IngestionConfig(ticker=config.ticker)
    qdrant = QdrantClient("localhost", port=6333)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT doc_id, collection FROM documents WHERE source_uri = %s AND status = 'active'",
                (config.object_key,),
            )
            rows = cur.fetchall()

    if not rows:
        context.log.warning(f"Không tìm thấy doc active cho: {config.object_key}")
        return {"deleted_docs": 0, "deleted_chunks": 0}

    total_chunks = 0
    for doc_id, collection in rows:
        collection = collection or cfg.qdrant_collection
        try:
            qdrant.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
            )
            context.log.info(f"Xóa chunk Qdrant: doc_id={doc_id} collection={collection}")
            total_chunks += 1
        except Exception as exc:
            context.log.warning(f"Qdrant delete warn: {exc}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET status='deleted', deleted_at=NOW() WHERE source_uri = %s AND status = 'active'",
                (config.object_key,),
            )

    context.log.info(f"Soft-deleted {len(rows)} doc(s) cho {config.object_key}")
    return {"deleted_docs": len(rows), "deleted_chunks": total_chunks}


# ── Jobs ──────────────────────────────────────────────────────────────────────

from pipeline.assets_news import news_raw, news_indexed, news_purge
from pipeline.assets_vnstock import (
    vnstock_financials, vnstock_prices,
    vnstock_financials_job, vnstock_prices_job,
    vnstock_financials_schedule, vnstock_prices_schedule,
)
from pipeline.assets_ohlcv import (
    ohlcv_daily_ingest,
    ohlcv_ingest_job,
    ohlcv_ingest_schedule,
)
from pipeline.assets_index import (
    market_index_daily_ingest,
    global_quotes_ingest,
    market_index_ingest_job,
    market_index_ingest_schedule,
)
from pipeline.assets_market_brief import (
    foreign_flows_ingest,
    corporate_events_ingest,
    daily_brief,
    foreign_flows_job,
    corporate_events_job,
    daily_brief_job,
    foreign_flows_schedule,
    corporate_events_schedule,
    daily_brief_schedule,
)

ingestion_job = define_asset_job(
    name="ingestion_job",
    selection=[raw_pdf, parsed_doc, embeddings, financial_facts],
)

delete_job = define_asset_job(
    name="delete_job",
    selection=[delete_doc],
)

ingestion_full_rebuild_job = define_asset_job(
    name="ingestion_full_rebuild_job",
    selection=[raw_pdf, parsed_doc, embeddings, financial_facts],
    config=RunConfig(
        ops={
            "raw_pdf": IngestionConfig(mode="full_rebuild"),
            "parsed_doc": IngestionConfig(mode="full_rebuild"),
            "embeddings": IngestionConfig(mode="full_rebuild"),
            "financial_facts": IngestionConfig(mode="full_rebuild"),
        }
    ),
)


# ── Schedule ──────────────────────────────────────────────────────────────────

daily_schedule = ScheduleDefinition(
    job=ingestion_job,
    cron_schedule="0 6 * * *",
    name="daily_ingestion_0600",
)

news_job = define_asset_job(
    name="news_job",
    selection=[news_raw, news_indexed],
)

news_purge_job = define_asset_job(
    name="news_purge_job",
    selection=[news_purge],
)

news_schedule = ScheduleDefinition(
    job=news_job,
    cron_schedule="0 */6 * * *",   # every 6h
    name="news_6h",
)

news_purge_schedule = ScheduleDefinition(
    job=news_purge_job,
    cron_schedule="0 2 * * 0",     # weekly Sunday 02:00
    name="news_purge_weekly",
)


# ── Sensor ────────────────────────────────────────────────────────────────────

@sensor(
    jobs=[ingestion_job, delete_job],
    name="minio_new_pdf_sensor",
    minimum_interval_seconds=300,
    description="Phát hiện PDF mới/sửa/xóa trong MinIO, kick off ingestion hoặc delete job.",
)
def minio_new_pdf_sensor(context):
    """Cursor = sorted PDF key list. Khi có key mới → run 1 job per file."""
    # TICKERS env var: danh sách ticker cần monitor, ví dụ "HPG,VCB,MWG"
    tickers = [t.strip().upper() for t in os.getenv("TICKERS", "HPG").split(",") if t.strip()]

    try:
        client = _minio()
        from dagster import RunRequest

        # Scan tất cả bucket của tất cả ticker
        # cursor format: "TICKER:key1,key2|TICKER2:key1,..."
        prev_cursor: dict[str, dict[str, str]] = _parse_cursor(context.cursor or "")
        new_cursor: dict[str, dict[str, str]] = {}
        run_requests = []

        for ticker in tickers:
            cfg = IngestionConfig(ticker=ticker)
            bucket = _bucket(cfg)

            if not client.bucket_exists(bucket):
                new_cursor[ticker] = {}
                continue

            # key → etag (etag = MD5 content hash từ MinIO)
            current: dict[str, str] = {
                obj.object_name: (obj.etag or "")
                for obj in client.list_objects(bucket, recursive=True)
                if obj.object_name and obj.object_name.lower().endswith(".pdf")
            }
            new_cursor[ticker] = current

            prev: dict[str, str] = prev_cursor.get(ticker, {})

            # Mới hoặc etag thay đổi → re-index
            to_index = [
                key for key, etag in current.items()
                if key not in prev or prev[key] != etag
            ]
            # Đã xóa khỏi MinIO → soft-delete
            to_delete = [key for key in prev if key not in current]

            if to_index:
                context.log.info(f"Sensor [{ticker}]: index/re-index {len(to_index)}: {to_index}")
            if to_delete:
                context.log.info(f"Sensor [{ticker}]: xóa {len(to_delete)}: {to_delete}")

            for key in to_index:
                parts = key.split("/")
                year = parts[0] if len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 4 else cfg.period
                run_cfg = IngestionConfig(mode="incremental", ticker=ticker, object_key=key, period=year)
                run_requests.append(
                    RunRequest(
                        run_key=f"{ticker}:{key}:{current[key]}",
                        job_name="ingestion_job",
                        run_config=RunConfig(
                            ops={
                                "raw_pdf": run_cfg,
                                "parsed_doc": run_cfg,
                                "embeddings": run_cfg,
                                "financial_facts": run_cfg,
                            }
                        ),
                    )
                )

            for key in to_delete:
                run_requests.append(
                    RunRequest(
                        run_key=f"delete:{ticker}:{key}",
                        run_config=RunConfig(
                            ops={"delete_doc": DeleteConfig(ticker=ticker, object_key=key)}
                        ),
                        job_name="delete_job",
                    )
                )

        return SensorResult(run_requests=run_requests, cursor=_serialize_cursor(new_cursor))

    except Exception as exc:
        context.log.warning(f"Sensor error: {exc}")
        return SensorResult(run_requests=[], cursor=context.cursor or "")


def _parse_cursor(cursor: str) -> dict[str, dict[str, str]]:
    """
    'HPG:a.pdf=etag1,b.pdf=etag2|VCB:c.pdf=etag3'
    → {'HPG': {'a.pdf': 'etag1', 'b.pdf': 'etag2'}, 'VCB': {'c.pdf': 'etag3'}}
    """
    result: dict[str, dict[str, str]] = {}
    for part in cursor.split("|"):
        if ":" not in part:
            continue
        ticker, entries_str = part.split(":", 1)
        etag_map: dict[str, str] = {}
        for entry in entries_str.split(","):
            if "=" in entry:
                key, etag = entry.split("=", 1)
                if key:
                    etag_map[key] = etag
        result[ticker] = etag_map
    return result


def _serialize_cursor(data: dict[str, dict[str, str]]) -> str:
    """
    {'HPG': {'a.pdf': 'etag1'}, 'VCB': {'c.pdf': 'etag3'}}
    → 'HPG:a.pdf=etag1|VCB:c.pdf=etag3'
    """
    parts = []
    for ticker, etag_map in data.items():
        entries = ",".join(f"{k}={v}" for k, v in etag_map.items())
        parts.append(f"{ticker}:{entries}")
    return "|".join(parts)


# ── Definitions ───────────────────────────────────────────────────────────────

defs = Definitions(
    assets=[raw_pdf, parsed_doc, embeddings, financial_facts, delete_doc,
            news_raw, news_indexed, news_purge,
            vnstock_financials, vnstock_prices,
            ohlcv_daily_ingest,
            market_index_daily_ingest, global_quotes_ingest,
            foreign_flows_ingest, corporate_events_ingest, daily_brief],
    jobs=[ingestion_job, ingestion_full_rebuild_job, delete_job,
          news_job, news_purge_job,
          vnstock_financials_job, vnstock_prices_job,
          ohlcv_ingest_job,
          market_index_ingest_job,
          foreign_flows_job, corporate_events_job, daily_brief_job],
    schedules=[daily_schedule, news_schedule, news_purge_schedule,
               vnstock_financials_schedule, vnstock_prices_schedule,
               ohlcv_ingest_schedule,
               market_index_ingest_schedule,
               foreign_flows_schedule, corporate_events_schedule, daily_brief_schedule],
    sensors=[minio_new_pdf_sensor],
)
