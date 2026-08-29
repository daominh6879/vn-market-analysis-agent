"""
tests/test_minio_pipeline.py — Test luồng MinIO thật → sensor → pipeline.

Các case:
  1. Insert file mới vào MinIO → sensor detect → ingestion_job trigger
  2. Update file (cùng key, nội dung khác) → sensor detect etag đổi → re-index
  3. Delete file khỏi MinIO → sensor detect key biến mất → delete_job trigger
  4. Insert file cho ticker mới → bucket riêng, collection riêng

Yêu cầu:
    docker compose up -d   (MinIO + Qdrant + Postgres)
    Ollama đang chạy với bge-m3
    .env đúng

Chạy:
    pytest tests/test_minio_pipeline.py -v
"""
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT_DIR = Path(__file__).parent.parent
_REAL_PDFS = sorted((ROOT_DIR / "reports").rglob("*.pdf"))

pytestmark = pytest.mark.integration

if not _REAL_PDFS:
    pytest.skip("Không tìm thấy PDF trong reports/", allow_module_level=True)

PDF_V1 = _REAL_PDFS[0]
PDF_V2 = _REAL_PDFS[1] if len(_REAL_PDFS) > 1 else _REAL_PDFS[0]

TICKER = "TST"
BUCKET = "tst-docs"
COLLECTION = "tst_structural"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mc():
    """MinIO client thật."""
    from pipeline.assets import _minio
    return _minio()


@pytest.fixture(autouse=True)
def clean_bucket(mc):
    """Xóa bucket test trước + sau mỗi test."""
    _wipe_bucket(mc, BUCKET)
    yield
    _wipe_bucket(mc, BUCKET)


@pytest.fixture(autouse=True)
def clean_qdrant():
    """Xóa collection test trước + sau mỗi test."""
    _wipe_collection(COLLECTION)
    yield
    _wipe_collection(COLLECTION)


def _wipe_bucket(mc, bucket: str):
    try:
        for obj in mc.list_objects(bucket, recursive=True):
            mc.remove_object(bucket, obj.object_name)
        mc.remove_bucket(bucket)
    except Exception:
        pass


def _wipe_collection(collection: str):
    try:
        from qdrant_client import QdrantClient
        QdrantClient("localhost", port=6333).delete_collection(collection)
    except Exception:
        pass


def _upload(mc, key: str, pdf_path: Path = None):
    """Upload PDF thật lên MinIO bucket BUCKET."""
    if not mc.bucket_exists(BUCKET):
        mc.make_bucket(BUCKET)
    data = (pdf_path or PDF_V1).read_bytes()
    mc.put_object(BUCKET, key, io.BytesIO(data), len(data))
    return data


def _get_etag(mc, key: str) -> str:
    stat = mc.stat_object(BUCKET, key)
    return stat.etag or ""


def _qdrant_count() -> int:
    from qdrant_client import QdrantClient
    try:
        return QdrantClient("localhost", port=6333).count(COLLECTION).count
    except Exception:
        return 0


def _run_sensor(cursor: str = "") -> tuple:
    """Chạy sensor thật với MinIO thật, trả (run_requests, new_cursor)."""
    from dagster import build_sensor_context
    from pipeline.assets import minio_new_pdf_sensor
    import os

    ctx = build_sensor_context(cursor=cursor)
    with patch("core.tickers.get_tickers", return_value=[TICKER]):
        result = minio_new_pdf_sensor(ctx)
    return result.run_requests, result.cursor


def _run_assets(key: str, mode: str = "full_rebuild"):
    """Materialize raw_pdf → parsed_doc → embeddings cho 1 file."""
    from dagster import materialize
    from pipeline.assets import raw_pdf, parsed_doc, embeddings, IngestionConfig

    cfg = IngestionConfig(
        ticker=TICKER, mode=mode, object_key=key,
        chunk_strategy="structural",
    ).model_dump()
    ops_cfg = {k: {"config": cfg} for k in ["raw_pdf", "parsed_doc", "embeddings"]}
    return materialize([raw_pdf, parsed_doc, embeddings], run_config={"ops": ops_cfg})


def _run_delete(key: str):
    """Materialize delete_doc cho 1 file."""
    from dagster import materialize
    from pipeline.assets import delete_doc, DeleteConfig

    cfg = DeleteConfig(ticker=TICKER, object_key=key).model_dump()
    return materialize([delete_doc], run_config={"ops": {"delete_doc": {"config": cfg}}})


# ══════════════════════════════════════════════════════════════════════════════
# Case 1: Insert file mới vào MinIO
# ══════════════════════════════════════════════════════════════════════════════

class TestInsertNewFile:
    def test_sensor_detects_new_file(self, mc):
        """Bucket trống → upload file → sensor phải tạo RunRequest."""
        # Bucket trống lúc đầu
        requests, _ = _run_sensor(cursor="")
        assert len(requests) == 0

        # Upload PDF
        _upload(mc, "2024/hpg_q4.pdf")

        # Sensor lần 2 → detect file mới
        requests, new_cursor = _run_sensor(cursor="")
        assert len(requests) == 1
        assert requests[0].job_name != "delete_job"

    def test_sensor_run_key_contains_key_and_etag(self, mc):
        """run_key = '{TICKER}:{key}:{etag}' để tránh duplicate run."""
        _upload(mc, "2024/hpg_q4.pdf")
        etag = _get_etag(mc, "2024/hpg_q4.pdf")

        requests, _ = _run_sensor(cursor="")
        assert "2024/hpg_q4.pdf" in requests[0].run_key
        assert etag in requests[0].run_key

    def test_pipeline_indexes_chunks_after_insert(self, mc):
        """Upload PDF → chạy assets → Qdrant có chunk."""
        _upload(mc, "2024/hpg_q4.pdf")

        result = _run_assets("2024/hpg_q4.pdf")
        assert result.success
        assert _qdrant_count() > 0

    def test_sensor_no_duplicate_on_second_poll(self, mc):
        """Sau khi index, sensor poll lại với cursor mới → không trigger lại."""
        _upload(mc, "2024/hpg_q4.pdf")

        requests_1, cursor_1 = _run_sensor(cursor="")
        assert len(requests_1) == 1

        # Poll lần 2 với cursor đã cập nhật
        requests_2, _ = _run_sensor(cursor=cursor_1)
        assert len(requests_2) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Case 2: Update file (cùng key, nội dung khác)
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateFile:
    def test_sensor_detects_etag_change(self, mc):
        """Upload v1 → cursor ghi nhận → upload v2 → sensor detect etag mới."""
        if PDF_V1 == PDF_V2:
            pytest.skip("Cần 2 PDF khác nhau")

        _upload(mc, "2024/hpg_q4.pdf", PDF_V1)
        _, cursor_after_v1 = _run_sensor(cursor="")

        # Upload file khác vào cùng key → etag thay đổi
        _upload(mc, "2024/hpg_q4.pdf", PDF_V2)
        requests, _ = _run_sensor(cursor=cursor_after_v1)

        assert len(requests) == 1, "Sensor phải detect etag đổi"
        assert requests[0].job_name != "delete_job"

    def test_update_replaces_chunks_not_accumulates(self, mc):
        """Index v1 → index v2 (cùng key) → chunk count không tăng gấp đôi."""
        if PDF_V1 == PDF_V2:
            pytest.skip("Cần 2 PDF khác nhau")

        # Index v1
        _upload(mc, "2024/hpg_q4.pdf", PDF_V1)
        _run_assets("2024/hpg_q4.pdf")
        count_v1 = _qdrant_count()
        assert count_v1 > 0

        # Upload v2 vào cùng key → re-index
        _upload(mc, "2024/hpg_q4.pdf", PDF_V2)
        _run_assets("2024/hpg_q4.pdf")
        count_v2 = _qdrant_count()

        # Chunk không tích lũy: count_v2 phải ≈ count_v2 của file v2, không phải v1+v2
        assert count_v2 < count_v1 * 2, (
            f"Chunk tích lũy: v1={count_v1}, sau update={count_v2} — chunk cũ chưa bị xóa"
        )

    def test_other_files_untouched_after_update(self, mc):
        """Update 1 file → file khác trong cùng bucket không bị ảnh hưởng."""
        if PDF_V1 == PDF_V2:
            pytest.skip("Cần 2 PDF khác nhau")

        _upload(mc, "2024/hpg_q4.pdf", PDF_V1)
        _upload(mc, "2023/hpg_q4.pdf", PDF_V1)

        # Cursor ghi nhận cả 2 file
        _, cursor = _run_sensor(cursor="")

        # Update chỉ file 2024
        _upload(mc, "2024/hpg_q4.pdf", PDF_V2)
        requests, _ = _run_sensor(cursor=cursor)

        assert len(requests) == 1
        assert "2024/hpg_q4.pdf" in requests[0].run_key
        assert "2023/hpg_q4.pdf" not in requests[0].run_key


# ══════════════════════════════════════════════════════════════════════════════
# Case 3: Delete file khỏi MinIO
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteFile:
    def test_sensor_detects_removed_key(self, mc):
        """Upload → cursor → xóa file khỏi MinIO → sensor tạo delete RunRequest."""
        _upload(mc, "2024/hpg_q4.pdf")
        _, cursor = _run_sensor(cursor="")

        # Xóa file khỏi MinIO
        mc.remove_object(BUCKET, "2024/hpg_q4.pdf")

        requests, _ = _run_sensor(cursor=cursor)
        assert len(requests) == 1
        assert requests[0].job_name == "delete_job"

    def test_delete_removes_chunks_from_qdrant(self, mc):
        """Index file → xóa file → delete_doc asset → Qdrant sạch."""
        _upload(mc, "2024/hpg_q4.pdf")
        _run_assets("2024/hpg_q4.pdf")
        assert _qdrant_count() > 0

        # Xóa file khỏi MinIO
        mc.remove_object(BUCKET, "2024/hpg_q4.pdf")

        # Chạy delete asset
        result = _run_delete("2024/hpg_q4.pdf")
        assert result.success
        assert _qdrant_count() == 0

    def test_delete_soft_deletes_postgres(self, mc):
        """Sau delete_doc, Postgres documents phải có status='deleted'."""
        from core.db import get_conn

        _upload(mc, "2024/hpg_q4.pdf")
        _run_assets("2024/hpg_q4.pdf")
        mc.remove_object(BUCKET, "2024/hpg_q4.pdf")
        _run_delete("2024/hpg_q4.pdf")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM documents WHERE source_uri = %s",
                    ("2024/hpg_q4.pdf",),
                )
                rows = cur.fetchall()

        assert len(rows) > 0, "Không tìm thấy record trong documents"
        assert all(r[0] == "deleted" for r in rows), f"Status không phải deleted: {rows}"

    def test_delete_other_files_unaffected(self, mc):
        """Xóa 1 file → file còn lại trong Qdrant không bị ảnh hưởng."""
        if PDF_V1 == PDF_V2:
            pytest.skip("Cần 2 PDF khác nội dung — cùng content → cùng doc_id → test không hợp lệ")

        # Dùng 2 PDF khác nội dung để có 2 doc_id khác nhau
        _upload(mc, "2024/hpg_q4.pdf", PDF_V1)
        _upload(mc, "2023/hpg_q4.pdf", PDF_V2)

        # Index CẢ 2 file
        _run_assets("2024/hpg_q4.pdf")
        _run_assets("2023/hpg_q4.pdf")
        count_before = _qdrant_count()
        assert count_before > 0

        # Chỉ xóa file 2024
        mc.remove_object(BUCKET, "2024/hpg_q4.pdf")
        _run_delete("2024/hpg_q4.pdf")
        count_after = _qdrant_count()

        # Còn chunk của 2023 → count giảm nhưng > 0
        assert count_after > 0, "Xóa 1 file làm mất chunk của file còn lại"
        assert count_after < count_before, "Chunk không giảm sau khi xóa"


# ══════════════════════════════════════════════════════════════════════════════
# Case 4: Insert file cho ticker mới
# ══════════════════════════════════════════════════════════════════════════════

class TestNewTicker:
    NEW_TICKER = "VCB"
    NEW_BUCKET = "vcb-docs"
    NEW_COLLECTION = "vcb_structural"

    @pytest.fixture(autouse=True)
    def clean_vcb(self, mc):
        _wipe_bucket(mc, self.NEW_BUCKET)
        _wipe_collection(self.NEW_COLLECTION)
        yield
        _wipe_bucket(mc, self.NEW_BUCKET)
        _wipe_collection(self.NEW_COLLECTION)

    def _upload_vcb(self, mc, key: str):
        if not mc.bucket_exists(self.NEW_BUCKET):
            mc.make_bucket(self.NEW_BUCKET)
        data = PDF_V1.read_bytes()
        mc.put_object(self.NEW_BUCKET, key, io.BytesIO(data), len(data))

    def test_new_ticker_uses_separate_bucket(self, mc):
        """VCB → bucket vcb-docs, không phải tst-docs."""
        from pipeline.assets import IngestionConfig, _bucket
        cfg = IngestionConfig(ticker=self.NEW_TICKER)
        assert _bucket(cfg) == self.NEW_BUCKET
        assert _bucket(cfg) != BUCKET

    def test_new_ticker_sensor_scans_its_bucket(self, mc):
        """Sensor với TICKERS=TST,VCB — upload VCB → detect đúng bucket."""
        from dagster import build_sensor_context
        from pipeline.assets import minio_new_pdf_sensor

        self._upload_vcb(mc, "2024/vcb_q4.pdf")

        ctx = build_sensor_context(cursor="")
        with patch("core.tickers.get_tickers", return_value=[TICKER, self.NEW_TICKER]):
            result = minio_new_pdf_sensor(ctx)

        vcb_requests = [r for r in result.run_requests if self.NEW_TICKER in r.run_key]
        assert len(vcb_requests) == 1

    def test_new_ticker_collection_isolated_from_existing(self, mc):
        """Index VCB không ảnh hưởng collection của TST."""
        from dagster import materialize
        from pipeline.assets import raw_pdf, parsed_doc, embeddings, IngestionConfig
        from qdrant_client import QdrantClient

        # Index TST
        _upload(mc, "2024/tst_q4.pdf")
        _run_assets("2024/tst_q4.pdf")
        count_tst_before = _qdrant_count()

        # Index VCB vào collection riêng
        self._upload_vcb(mc, "2024/vcb_q4.pdf")
        cfg = IngestionConfig(
            ticker=self.NEW_TICKER, mode="full_rebuild",
            object_key="2024/vcb_q4.pdf", chunk_strategy="structural",
        ).model_dump()
        ops_cfg = {k: {"config": cfg} for k in ["raw_pdf", "parsed_doc", "embeddings"]}
        materialize([raw_pdf, parsed_doc, embeddings], run_config={"ops": ops_cfg})

        qdrant = QdrantClient("localhost", port=6333)
        count_vcb = qdrant.count(self.NEW_COLLECTION).count
        count_tst_after = _qdrant_count()

        assert count_vcb > 0, "VCB không có chunk"
        assert count_tst_after == count_tst_before, (
            f"Index VCB làm thay đổi TST collection: {count_tst_before} → {count_tst_after}"
        )
