"""
data/contracts.py — Bài 9: Schema Pydantic cho pipeline index.

doc_id = sha256(file_bytes)[:16]  — xác định từ nội dung, không từ tên file.
Chạy lại cùng file → cùng doc_id → upsert đè, không nhân đôi.
"""
import hashlib

from pydantic import BaseModel, field_validator


class ParsedDoc(BaseModel):
    doc_id: str
    content: str
    source_path: str
    parsed_at: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        assert len(v.strip()) > 0, "Content rỗng"
        return v


def compute_doc_id(content: bytes) -> str:
    """sha256 của byte content, lấy 16 ký tự đầu."""
    return hashlib.sha256(content).hexdigest()[:16]
