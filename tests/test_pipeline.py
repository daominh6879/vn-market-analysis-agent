"""
tests/test_pipeline.py — Bài 13: Test pipeline Dagster.

Unit tests (không cần service):
    pytest tests/test_pipeline.py -v -m "not integration"

Integration tests (cần Qdrant + Ollama + MinIO):
    pytest tests/test_pipeline.py -v -m integration
"""
import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.assets import (
    DeleteConfig,
    IngestionConfig,
    _bucket,
    _collection,
    _parse_cursor,
    _serialize_cursor,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_minio_object(key: str, etag: str = "etag_abc", size: int = 1024):
    obj = MagicMock()
    obj.object_name = key
    obj.etag = etag
    obj.size = size
    return obj


def _build_sensor_context(cursor: str = ""):
    from dagster import build_sensor_context
    ctx = build_sensor_context()
    ctx._cursor = cursor
    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# 1. Cursor helpers — pure functions, không cần service
# ══════════════════════════════════════════════════════════════════════════════

class TestCursor:
    def test_roundtrip_single_ticker(self):
        data = {"HPG": {"2024/a.pdf": "etag1", "2024/b.pdf": "etag2"}}
        assert _parse_cursor(_serialize_cursor(data)) == data

    def test_roundtrip_multi_ticker(self):
        data = {
            "HPG": {"2024/hpg.pdf": "abc123"},
            "VCB": {"2024/vcb.pdf": "def456", "2023/vcb.pdf": "ghi789"},
        }
        assert _parse_cursor(_serialize_cursor(data)) == data

    def test_empty_cursor(self):
        assert _parse_cursor("") == {}

    def test_empty_bucket(self):
        data = {"HPG": {}}
        result = _parse_cursor(_serialize_cursor(data))
        assert result.get("HPG", {}) == {}

    def test_key_with_slash(self):
        """Object key MinIO có thể chứa dấu / (thư mục ảo)."""
        data = {"HPG": {"reports/2024/q4/hpg_bctc.pdf": "etag1"}}
        assert _parse_cursor(_serialize_cursor(data)) == data


# ══════════════════════════════════════════════════════════════════════════════
# 2. Bucket / collection naming
# ══════════════════════════════════════════════════════════════════════════════

class TestNaming:
    def test_bucket_lowercase(self):
        assert _bucket(IngestionConfig(ticker="HPG")) == "hpg-docs"
        assert _bucket(IngestionConfig(ticker="VCB")) == "vcb-docs"
        assert _bucket(IngestionConfig(ticker="MWG")) == "mwg-docs"

    def test_collection_includes_strategy(self):
        assert _collection(IngestionConfig(ticker="HPG", chunk_strategy="structural")) == "hpg_structural"
        assert _collection(IngestionConfig(ticker="HPG", chunk_strategy="fixed")) == "hpg_fixed"
        assert _collection(IngestionConfig(ticker="VCB", chunk_strategy="structural")) == "vcb_structural"

    def test_ticker_isolation(self):
        """Hai ticker khác nhau → bucket và collection khác nhau."""
        hpg = IngestionConfig(ticker="HPG")
        vcb = IngestionConfig(ticker="VCB")
        assert _bucket(hpg) != _bucket(vcb)
        assert _collection(hpg) != _collection(vcb)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Sensor — detect new / updated / deleted file
# ══════════════════════════════════════════════════════════════════════════════

class TestSensor:
    def _run_sensor(self, minio_objects: list, cursor: str = "", tickers: str = "HPG"):
        """Chạy sensor với mock MinIO, trả (run_requests, new_cursor)."""
        from dagster import build_sensor_context
        from pipeline.assets import minio_new_pdf_sensor

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_client.list_objects.return_value = minio_objects

        ctx = build_sensor_context(cursor=cursor)

        with patch("pipeline.assets._minio", return_value=mock_client), \
             patch.dict("os.environ", {"TICKERS": tickers}):
            result = minio_new_pdf_sensor(ctx)

        return result.run_requests, result.cursor

    # ── Add new PDF ───────────────────────────────────────────────────────────

    def test_new_pdf_triggers_ingestion(self):
        """PDF mới trong bucket → 1 RunRequest cho ingestion_job."""
        objs = [_make_minio_object("2024/hpg_q4.pdf", "etag1")]
        requests, _ = self._run_sensor(objs, cursor="")

        assert len(requests) == 1
        assert requests[0].job_name != "delete_job"

    def test_new_pdf_run_key_includes_etag(self):
        """run_key phải bao gồm etag — chạy lại khi nội dung đổi."""
        objs = [_make_minio_object("2024/hpg_q4.pdf", "etag_v1")]
        requests, _ = self._run_sensor(objs, cursor="")
        assert "etag_v1" in requests[0].run_key

    def test_no_new_pdf_no_request(self):
        """Cursor khớp bucket hiện tại → không trigger."""
        objs = [_make_minio_object("2024/hpg_q4.pdf", "etag1")]
        existing_cursor = _serialize_cursor({"HPG": {"2024/hpg_q4.pdf": "etag1"}})
        requests, _ = self._run_sensor(objs, cursor=existing_cursor)
        assert len(requests) == 0

    # ── Update PDF (same key, etag changed) ───────────────────────────────────

    def test_updated_pdf_triggers_reindex(self):
        """Cùng key, etag mới → trigger ingestion lại."""
        objs = [_make_minio_object("2024/hpg_q4.pdf", "etag_v2")]
        old_cursor = _serialize_cursor({"HPG": {"2024/hpg_q4.pdf": "etag_v1"}})
        requests, _ = self._run_sensor(objs, cursor=old_cursor)

        assert len(requests) == 1
        assert "etag_v2" in requests[0].run_key

    def test_update_does_not_affect_other_files(self):
        """Update 1 file → chỉ 1 request, file kia không bị đụng."""
        objs = [
            _make_minio_object("2024/hpg_q4.pdf", "etag_v2"),   # updated
            _make_minio_object("2023/hpg_q4.pdf", "etag_old"),  # unchanged
        ]
        old_cursor = _serialize_cursor({
            "HPG": {
                "2024/hpg_q4.pdf": "etag_v1",
                "2023/hpg_q4.pdf": "etag_old",
            }
        })
        requests, _ = self._run_sensor(objs, cursor=old_cursor)
        assert len(requests) == 1
        assert "2024/hpg_q4.pdf" in requests[0].run_key

    # ── Delete PDF (key removed from MinIO) ───────────────────────────────────

    def test_deleted_pdf_triggers_delete_job(self):
        """Key biến mất khỏi bucket → RunRequest cho delete_job."""
        objs = []  # bucket trống
        old_cursor = _serialize_cursor({"HPG": {"2024/hpg_q4.pdf": "etag1"}})
        requests, _ = self._run_sensor(objs, cursor=old_cursor)

        assert len(requests) == 1
        assert requests[0].job_name == "delete_job"

    def test_delete_run_key_unique(self):
        """Delete run_key phải bắt đầu bằng 'delete:' để không xung đột ingestion."""
        objs = []
        old_cursor = _serialize_cursor({"HPG": {"2024/hpg_q4.pdf": "etag1"}})
        requests, _ = self._run_sensor(objs, cursor=old_cursor)
        assert requests[0].run_key.startswith("delete:")

    # ── Add new ticker ────────────────────────────────────────────────────────

    def test_new_ticker_separate_bucket(self):
        """Thêm VCB vào TICKERS → sensor scan bucket vcb-docs riêng."""
        mock_client = MagicMock()

        def bucket_exists(name):
            return name == "hpg-docs"  # vcb-docs chưa tồn tại

        def list_objects(bucket, recursive):
            if bucket == "hpg-docs":
                return [_make_minio_object("2024/hpg.pdf", "etag1")]
            return []

        mock_client.bucket_exists.side_effect = bucket_exists
        mock_client.list_objects.side_effect = list_objects

        from dagster import build_sensor_context
        from pipeline.assets import minio_new_pdf_sensor

        ctx = build_sensor_context(cursor="")
        with patch("pipeline.assets._minio", return_value=mock_client), \
             patch.dict("os.environ", {"TICKERS": "HPG,VCB"}):
            result = minio_new_pdf_sensor(ctx)

        # Chỉ HPG có file → 1 request, VCB không có → 0
        assert len(result.run_requests) == 1
        assert "hpg" in result.run_requests[0].run_key.lower()

    def test_new_ticker_does_not_affect_existing(self):
        """Thêm ticker mới → ticker cũ không bị trigger lại."""
        mock_client = MagicMock()

        def bucket_exists(name):
            return True

        def list_objects(bucket, recursive):
            if bucket == "hpg-docs":
                return [_make_minio_object("2024/hpg.pdf", "etag1")]
            if bucket == "vcb-docs":
                return [_make_minio_object("2024/vcb.pdf", "etag_vcb")]
            return []

        mock_client.bucket_exists.side_effect = bucket_exists
        mock_client.list_objects.side_effect = list_objects

        from dagster import build_sensor_context
        from pipeline.assets import minio_new_pdf_sensor

        # HPG đã indexed, VCB là ticker mới (chưa có trong cursor)
        old_cursor = _serialize_cursor({"HPG": {"2024/hpg.pdf": "etag1"}})
        ctx = build_sensor_context(cursor=old_cursor)

        with patch("pipeline.assets._minio", return_value=mock_client), \
             patch.dict("os.environ", {"TICKERS": "HPG,VCB"}):
            result = minio_new_pdf_sensor(ctx)

        # Chỉ VCB mới → 1 request, HPG không trigger
        assert len(result.run_requests) == 1
        assert "VCB" in result.run_requests[0].run_key

    # ── Cursor persistence ────────────────────────────────────────────────────

    def test_cursor_updated_after_run(self):
        """Sau khi chạy, cursor phải ghi nhận trạng thái mới."""
        objs = [_make_minio_object("2024/hpg_q4.pdf", "etag_new")]
        _, new_cursor = self._run_sensor(objs, cursor="")
        parsed = _parse_cursor(new_cursor)
        assert parsed["HPG"]["2024/hpg_q4.pdf"] == "etag_new"

    def test_cursor_preserves_deleted_ticker_not_in_tickers(self):
        """Ticker không còn trong TICKERS → không xuất hiện trong cursor mới."""
        objs = [_make_minio_object("2024/hpg.pdf", "etag1")]
        old_cursor = _serialize_cursor({"HPG": {"2024/hpg.pdf": "etag1"}})
        _, new_cursor = self._run_sensor(objs, cursor=old_cursor, tickers="HPG")
        parsed = _parse_cursor(new_cursor)
        assert "VCB" not in parsed


# ══════════════════════════════════════════════════════════════════════════════
# 4. Filter unindexed — mock Postgres
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterUnindexed:
    def test_excludes_already_indexed(self):
        from dagster import build_asset_context
        from pipeline.assets import _filter_unindexed

        objects = [
            {"key": "2024/hpg_q4.pdf", "etag": "e1", "size": 100},
            {"key": "2024/hpg_q3.pdf", "etag": "e2", "size": 100},
            {"key": "2023/hpg_q4.pdf", "etag": "e3", "size": 100},
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("2024/hpg_q4.pdf",), ("2024/hpg_q3.pdf",)]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        ctx = build_asset_context()
        with patch("pipeline.assets.get_conn", return_value=mock_conn, create=True):
            from core.db import get_conn
            with patch("core.db.get_conn", return_value=mock_conn):
                result = _filter_unindexed(objects, ctx)

        # Chỉ còn 2023/hpg_q4.pdf chưa indexed
        assert len(result) == 1
        assert result[0]["key"] == "2023/hpg_q4.pdf"

    def test_fallback_when_postgres_down(self):
        """Postgres không kết nối được → xử lý tất cả (không drop file)."""
        from dagster import build_asset_context
        from pipeline.assets import _filter_unindexed

        objects = [{"key": "2024/hpg.pdf", "etag": "e1", "size": 100}]
        ctx = build_asset_context()

        with patch("pipeline.assets._filter_unindexed",
                   side_effect=Exception("Connection refused")):
            # _filter_unindexed tự catch exception và return tất cả objects
            # Test trực tiếp behavior khi exception
            pass

        # Verify: khi get_conn raise → trả về objects gốc
        with patch("core.db.get_conn", side_effect=Exception("DB down")):
            result = _filter_unindexed(objects, ctx)
        assert result == objects


# ══════════════════════════════════════════════════════════════════════════════
# 5. IngestionConfig defaults
# ══════════════════════════════════════════════════════════════════════════════

class TestIngestionConfig:
    def test_default_ticker_hpg(self):
        cfg = IngestionConfig()
        assert cfg.ticker == "HPG"

    def test_default_mode_incremental(self):
        cfg = IngestionConfig()
        assert cfg.mode == "incremental"

    def test_custom_ticker(self):
        cfg = IngestionConfig(ticker="VCB")
        assert _bucket(cfg) == "vcb-docs"
        assert "vcb" in _collection(cfg)

    def test_delete_config(self):
        cfg = DeleteConfig(ticker="HPG", object_key="2024/hpg_q4.pdf")
        assert cfg.ticker == "HPG"
        assert cfg.object_key == "2024/hpg_q4.pdf"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Integration tests — cần Qdrant + Ollama + MinIO + Postgres
# ══════════════════════════════════════════════════════════════════════════════

ROOT_DIR = Path(__file__).parent.parent

# PDF thật để test — dùng file có sẵn trong reports/
_REAL_PDFS = sorted((ROOT_DIR / "reports").rglob("*.pdf"))
_TEST_PDF = _REAL_PDFS[0] if _REAL_PDFS else None


@pytest.mark.integration
@pytest.mark.skipif(_TEST_PDF is None, reason="Không tìm thấy PDF trong reports/ để test")
class TestPipelineIntegration:
    """
    Chạy: pytest tests/test_pipeline.py -v -m integration
    Yêu cầu: docker compose up -d, Ollama với bge-m3, .env đúng

    PDF fixture: dùng file thật từ reports/ (không fake bytes).
    Mỗi test dùng ticker "TST" → bucket test-docs, collection tst_structural.
    """

    TICKER = "TST"
    COLLECTION = "tst_structural"
    BUCKET = "tst-docs"

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Dọn Qdrant collection và MinIO bucket trước + sau mỗi test."""
        self._wipe()
        yield
        self._wipe()

    def _wipe(self):
        try:
            from qdrant_client import QdrantClient
            QdrantClient("localhost", port=6333).delete_collection(self.COLLECTION)
        except Exception:
            pass
        try:
            from pipeline.assets import _minio
            mc = _minio()
            for obj in mc.list_objects(self.BUCKET, recursive=True):
                mc.remove_object(self.BUCKET, obj.object_name)
            mc.remove_bucket(self.BUCKET)
        except Exception:
            pass

    def _upload_pdf(self, key: str, pdf_path: Path = None):
        """Upload PDF thật lên MinIO. pdf_path=None → dùng _TEST_PDF."""
        import io
        from pipeline.assets import _minio
        mc = _minio()
        if not mc.bucket_exists(self.BUCKET):
            mc.make_bucket(self.BUCKET)
        src = pdf_path or _TEST_PDF
        data = src.read_bytes()
        mc.put_object(self.BUCKET, key, io.BytesIO(data), len(data))

    def _get_qdrant_count(self):
        from qdrant_client import QdrantClient
        try:
            return QdrantClient("localhost", port=6333).count(self.COLLECTION).count
        except Exception:
            return 0

    def test_new_pdf_indexed(self):
        """Upload PDF → materialize pipeline → chunk có trong Qdrant."""
        from dagster import materialize
        from pipeline.assets import raw_pdf, parsed_doc, embeddings, financial_facts

        self._upload_pdf("2024/test_q4.pdf")
        cfg = IngestionConfig(ticker=self.TICKER, mode="full_rebuild", object_key="2024/test_q4.pdf")
        result = materialize(
            [raw_pdf, parsed_doc, embeddings],
            run_config={"ops": {
                "raw_pdf": {"config": cfg.model_dump()},
                "parsed_doc": {"config": cfg.model_dump()},
                "embeddings": {"config": cfg.model_dump()},
            }},
        )
        assert result.success
        assert self._get_qdrant_count() > 0

    def test_update_pdf_replaces_chunks(self):
        """Upload v1 → index → upload v2 (PDF khác, etag mới) → re-index → chunk cũ không còn."""
        from dagster import materialize
        from pipeline.assets import raw_pdf, parsed_doc, embeddings

        if len(_REAL_PDFS) < 2:
            pytest.skip("Cần ít nhất 2 PDF trong reports/ để test update")

        key = "2024/test_update.pdf"
        cfg_dict = IngestionConfig(ticker=self.TICKER, mode="full_rebuild", object_key=key).model_dump()
        ops_cfg = {k: {"config": cfg_dict} for k in ["raw_pdf", "parsed_doc", "embeddings"]}

        # Index lần 1 — PDF thứ nhất
        self._upload_pdf(key, _REAL_PDFS[0])
        materialize([raw_pdf, parsed_doc, embeddings], run_config={"ops": ops_cfg})
        count_v1 = self._get_qdrant_count()
        assert count_v1 > 0, "Index lần 1 không tạo ra chunk"

        # Upload PDF khác vào cùng key (etag thay đổi) → re-index
        self._upload_pdf(key, _REAL_PDFS[1])
        materialize([raw_pdf, parsed_doc, embeddings], run_config={"ops": ops_cfg})
        count_v2 = self._get_qdrant_count()

        # Chunk count phải gần bằng — không tích lũy chunk cũ lẫn mới
        assert abs(count_v2 - count_v1) < count_v1 * 0.5, (
            f"Chunk tăng đột biến: v1={count_v1}, v2={count_v2} — chunk cũ chưa bị xóa"
        )

    def test_delete_pdf_removes_chunks(self):
        """Upload → index → delete_doc asset → Qdrant sạch."""
        from dagster import materialize
        from pipeline.assets import raw_pdf, parsed_doc, embeddings, delete_doc

        key = "2024/test_delete.pdf"
        ingest_cfg = IngestionConfig(ticker=self.TICKER, mode="full_rebuild", object_key=key).model_dump()

        self._upload_pdf(key)
        materialize(
            [raw_pdf, parsed_doc, embeddings],
            run_config={"ops": {k: {"config": ingest_cfg} for k in ["raw_pdf", "parsed_doc", "embeddings"]}},
        )
        assert self._get_qdrant_count() > 0

        # Trigger delete
        del_cfg = DeleteConfig(ticker=self.TICKER, object_key=key).model_dump()
        materialize(
            [delete_doc],
            run_config={"ops": {"delete_doc": {"config": del_cfg}}},
        )
        assert self._get_qdrant_count() == 0

    def test_two_tickers_isolated(self):
        """HPG và VCB index vào collection riêng — không ảnh hưởng nhau."""
        from dagster import materialize
        from pipeline.assets import raw_pdf, parsed_doc, embeddings, _minio as get_minio
        from qdrant_client import QdrantClient

        qdrant = QdrantClient("localhost", port=6333)
        hpg_collection = "hpg_structural"
        vcb_collection = "vcb_structural"

        # Dọn trước
        for col in [hpg_collection, vcb_collection]:
            try:
                qdrant.delete_collection(col)
            except Exception:
                pass

        mc = get_minio()
        for bucket in ["hpg-docs", "vcb-docs"]:
            if not mc.bucket_exists(bucket):
                mc.make_bucket(bucket)

        pdf_bytes = _TEST_PDF.read_bytes()
        import io as _io
        mc.put_object("hpg-docs", "2024/hpg.pdf", _io.BytesIO(pdf_bytes), len(pdf_bytes))
        mc.put_object("vcb-docs", "2024/vcb.pdf", _io.BytesIO(pdf_bytes), len(pdf_bytes))

        for ticker, bucket in [("HPG", "hpg-docs"), ("VCB", "vcb-docs")]:
            cfg = IngestionConfig(ticker=ticker, mode="full_rebuild", object_key=f"2024/{ticker.lower()}.pdf")
            materialize(
                [raw_pdf, parsed_doc, embeddings],
                run_config={"ops": {k: {"config": cfg.model_dump()} for k in ["raw_pdf", "parsed_doc", "embeddings"]}},
            )

        hpg_count = qdrant.count(hpg_collection).count
        vcb_count = qdrant.count(vcb_collection).count

        assert hpg_count > 0, "HPG không có chunk"
        assert vcb_count > 0, "VCB không có chunk"

        # Cleanup extra
        for col in [hpg_collection, vcb_collection]:
            try:
                qdrant.delete_collection(col)
            except Exception:
                pass
        for bucket in ["hpg-docs", "vcb-docs"]:
            try:
                for obj in mc.list_objects(bucket, recursive=True):
                    mc.remove_object(bucket, obj.object_name)
                mc.remove_bucket(bucket)
            except Exception:
                pass
