"""
tests/test_idempotent.py — Bài 9: pipeline index phải idempotent.

Yêu cầu services đang chạy: Qdrant (localhost:6333) + Ollama (bge-m3).
Chạy: uv run pytest tests/test_idempotent.py -v
"""
import sys
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.contracts import compute_doc_id
from rag.index import run

TEST_COLLECTION = "test_idempotent_b9"
EMBED_MODEL = "bge-m3"
TEST_TEXT = (
    "Doanh thu thuần của HPG năm 2024 đạt 165.000 tỷ đồng, tăng 15% so với năm trước. "
    "Lợi nhuận gộp đạt 22.000 tỷ đồng với biên lợi nhuận gộp 13,3%. "
    "Tổng tài sản tăng lên 165.000 tỷ đồng. Nợ phải trả là 80.000 tỷ đồng. "
    "Vốn chủ sở hữu đạt 85.000 tỷ đồng. ROE đạt 18,5%. "
) * 10  # ~700 ký tự × 10 = ~7000 ký tự → vài chunk với fixed_512


@pytest.fixture(autouse=True)
def clean_collection():
    client = QdrantClient("localhost", port=6333)
    try:
        client.delete_collection(TEST_COLLECTION)
    except Exception:
        pass
    yield
    try:
        client.delete_collection(TEST_COLLECTION)
    except Exception:
        pass


def run_pipeline(text: str) -> QdrantClient:
    client = QdrantClient("localhost", port=6333)
    doc_id = compute_doc_id(text.encode())
    run(text, TEST_COLLECTION, "fixed", EMBED_MODEL, None, client, doc_id=doc_id)
    return client


def get_state(client: QdrantClient) -> tuple[int, list[str]]:
    count = client.count(TEST_COLLECTION).count
    records, _ = client.scroll(
        TEST_COLLECTION, limit=1000, with_payload=False, with_vectors=False
    )
    ids = sorted(str(r.id) for r in records)
    return count, ids


def test_idempotent():
    run_pipeline(TEST_TEXT)
    client = QdrantClient("localhost", port=6333)
    count_1, ids_1 = get_state(client)

    run_pipeline(TEST_TEXT)
    count_2, ids_2 = get_state(client)

    assert count_1 == count_2, f"Vector count changed: {count_1} → {count_2}"
    assert ids_1 == ids_2, "Point IDs changed between runs"


def test_changed_content_changes_doc_id():
    """Thay đổi nội dung → doc_id mới → chunk IDs mới."""
    doc_id_a = compute_doc_id(TEST_TEXT.encode())
    doc_id_b = compute_doc_id((TEST_TEXT + "X").encode())
    assert doc_id_a != doc_id_b


def test_empty_content_rejected():
    """Chunk rỗng bị chặn bởi schema."""
    from data.contracts import ParsedDoc
    import pytest as pt

    with pt.raises(Exception):
        ParsedDoc(doc_id="abc", content="   ", source_path="x", parsed_at="2024")
