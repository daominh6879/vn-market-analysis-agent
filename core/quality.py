"""
data/quality.py — Bài 11: Cửa lọc chất lượng trước khi dữ liệu vào index.

Rủi ro thật: file được index thành rác (PDF scan parse ra vài dòng vô nghĩa),
không có dấu hiệu lỗi, agent trả lời sai từ nó.

File không qua → chuyển vào MinIO quarantine/ + ghi lý do vào Postgres.

Yêu cầu:
    uv add minio

Chạy migration trước:
    python data/db.py  -- hoặc chạy infra/migrations/002_quarantine_log.sql

Usage:
    python data/quality.py --file <path> [--doc-id <id>]
    python data/quality.py --list-quarantine
    python data/quality.py --run-migration
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Ngưỡng ────────────────────────────────────────────────────────────────────

CHAR_RATIO_MIN = 0.30         # < 30% ký tự đọc được → nghi PDF scan
CHARS_PER_PAGE_MIN = 100      # < 100 ký tự/trang → nghi PDF scan
DUPLICATE_RATIO_MAX = 0.20    # > 20% chunk trùng → bất thường
MAX_PAGES = 400               # > 400 trang → cảnh báo (không chặn)


# ── Dataclass kết quả ─────────────────────────────────────────────────────────

@dataclass
class QualityResult:
    passed: bool
    reason: str | None = None          # None nếu passed
    quarantine_path: str | None = None  # MinIO object path nếu bị cách ly
    char_ratio: float | None = None
    chars_per_page: float | None = None


# ── Các hàm kiểm tra ──────────────────────────────────────────────────────────

def check_char_ratio(text: str) -> float:
    """Tỉ lệ ký tự in được (không tính khoảng trắng). PDF scan thường < 0.3."""
    if not text:
        return 0.0
    readable = sum(1 for c in text if c.isprintable() and not c.isspace())
    return readable / len(text)


def check_chars_per_page(text: str, num_pages: int) -> float:
    """Số ký tự trung bình mỗi trang. < 100 là nghi ngờ PDF scan."""
    return len(text) / max(num_pages, 1)


def check_has_table(text: str) -> bool:
    """Có bảng markdown/pipe không. Tìm pattern: dòng có ít nhất 2 pipe hoặc tab + số."""
    pipe_pattern = re.compile(r"\|.+\|")
    number_line = re.compile(r"\d{3,}")  # số ≥ 3 chữ số (tiêu chí sơ bộ)
    has_pipe = bool(pipe_pattern.search(text))
    has_numbers = bool(number_line.search(text))
    return has_pipe and has_numbers


def check_duplicate_ratio(chunks: list[str]) -> float:
    """Tỉ lệ chunk trùng lặp. > 0.2 là bất thường."""
    if not chunks:
        return 0.0
    unique = len(set(chunks))
    return 1.0 - unique / len(chunks)


# ── Hàm đánh giá tổng hợp ────────────────────────────────────────────────────

def assess_quality(
    text: str,
    num_pages: int,
    chunks: list[str] | None = None,
) -> QualityResult:
    """
    Đánh giá chất lượng text đã parse từ 1 file.

    Trả về QualityResult.passed=False kèm reason nếu không đạt.
    Ca nguy hiểm nhất: PDF scan — check chars_per_page bắt được nó.
    """
    if not text.strip():
        return QualityResult(
            passed=False,
            reason="Nội dung rỗng sau parse",
            char_ratio=0.0,
            chars_per_page=0.0,
        )

    ratio = check_char_ratio(text)
    cpp = check_chars_per_page(text, num_pages)

    if cpp < CHARS_PER_PAGE_MIN:
        return QualityResult(
            passed=False,
            reason=f"Nghi ngờ PDF scan — {cpp:.0f} ký tự/trang (ngưỡng: {CHARS_PER_PAGE_MIN})",
            char_ratio=ratio,
            chars_per_page=cpp,
        )

    if ratio < CHAR_RATIO_MIN:
        return QualityResult(
            passed=False,
            reason=f"Quá nhiều ký tự không đọc được: {ratio:.0%} (ngưỡng: {CHAR_RATIO_MIN:.0%})",
            char_ratio=ratio,
            chars_per_page=cpp,
        )

    if chunks:
        dup = check_duplicate_ratio(chunks)
        if dup > DUPLICATE_RATIO_MAX:
            return QualityResult(
                passed=False,
                reason=f"Tỉ lệ chunk trùng lặp quá cao: {dup:.0%} (ngưỡng: {DUPLICATE_RATIO_MAX:.0%})",
                char_ratio=ratio,
                chars_per_page=cpp,
            )

    return QualityResult(passed=True, char_ratio=ratio, chars_per_page=cpp)


# ── MinIO helper ──────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    from core.config import settings
    return Minio(
        "localhost:9000",
        access_key=settings.MINIO_ROOT_USER,
        secret_key=settings.MINIO_ROOT_PASSWORD,
        secure=False,
    )


QUARANTINE_BUCKET = "quarantine"


def _ensure_quarantine_bucket(client) -> None:
    if not client.bucket_exists(QUARANTINE_BUCKET):
        client.make_bucket(QUARANTINE_BUCKET)


def upload_to_quarantine(file_path: str, doc_id: str) -> str:
    """Upload file vào MinIO quarantine/, trả về object path."""
    client = _minio_client()
    _ensure_quarantine_bucket(client)

    suffix = Path(file_path).suffix
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    object_name = f"{ts}_{doc_id}{suffix}"

    client.fput_object(QUARANTINE_BUCKET, object_name, file_path)
    return f"{QUARANTINE_BUCKET}/{object_name}"


# ── Postgres helper ───────────────────────────────────────────────────────────

def log_quarantine(
    doc_id: str,
    source_path: str,
    reason: str,
    quarantine_path: str,
    char_ratio: float | None,
    chars_per_page: float | None,
) -> None:
    """Ghi lý do cách ly vào Postgres."""
    from data.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quarantine_log
                    (doc_id, source_path, reason, char_ratio, chars_per_page, quarantined_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (doc_id, source_path, reason, char_ratio, chars_per_page),
            )


def list_quarantine() -> None:
    """In danh sách file bị cách ly."""
    from data.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT doc_id, source_path, reason, char_ratio, chars_per_page, quarantined_at
                FROM quarantine_log
                ORDER BY quarantined_at DESC
                LIMIT 100
                """
            )
            rows = cur.fetchall()

    if not rows:
        print("Danh sách cách ly trống.")
        return

    print(f"\n{'doc_id':<18} {'source_path':<35} {'reason':<50} {'ratio':>6} {'cpp':>7} {'lúc'}")
    print("-" * 130)
    for doc_id, src, reason, ratio, cpp, ts in rows:
        r = f"{ratio:.2f}" if ratio is not None else "—"
        c = f"{cpp:.0f}" if cpp is not None else "—"
        print(f"{doc_id:<18} {str(src):<35} {reason:<50} {r:>6} {c:>7} {ts}")


# ── Pipeline entry point ──────────────────────────────────────────────────────

def process_file(file_path: str) -> QualityResult:
    """
    Parse 1 file PDF, đánh giá chất lượng.
    Nếu không đạt: upload MinIO quarantine + log Postgres.
    Trả về QualityResult để caller quyết định có index hay không.
    """
    import pymupdf

    path = Path(file_path)
    raw_bytes = path.read_bytes()
    doc_id = hashlib.sha256(raw_bytes).hexdigest()[:16]

    # Đếm trang
    try:
        doc = pymupdf.open(str(path))
        num_pages = doc.page_count
        doc.close()
    except Exception:
        num_pages = 1

    # Parse text đơn giản để kiểm tra (không cần markdown đẹp)
    try:
        import pymupdf4llm
        text = pymupdf4llm.to_markdown(str(path))
    except Exception as e:
        return QualityResult(passed=False, reason=f"Parse thất bại: {e}")

    result = assess_quality(text, num_pages)

    if not result.passed:
        try:
            q_path = upload_to_quarantine(file_path, doc_id)
            result.quarantine_path = q_path
            log_quarantine(
                doc_id=doc_id,
                source_path=str(path),
                reason=result.reason,
                quarantine_path=q_path,
                char_ratio=result.char_ratio,
                chars_per_page=result.chars_per_page,
            )
            print(f"QUARANTINE  {path.name}  →  {q_path}")
            print(f"  Lý do: {result.reason}")
        except Exception as e:
            print(f"  [warn] Không upload được MinIO: {e}", file=sys.stderr)
            print(f"  Lý do cách ly: {result.reason}")
    else:
        print(f"OK  {path.name}  (ratio={result.char_ratio:.2f}, cpp={result.chars_per_page:.0f})")

    return result


# ── Migration helper ──────────────────────────────────────────────────────────

def run_migration() -> None:
    from data.db import run_migration as _run

    sql_path = ROOT / "infra" / "migrations" / "002_quarantine_log.sql"
    _run(str(sql_path))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bài 11 — kiểm tra chất lượng file")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path đến file PDF cần kiểm tra")
    group.add_argument("--list-quarantine", action="store_true", help="In danh sách file bị cách ly")
    group.add_argument("--run-migration", action="store_true", help="Chạy migration 002_quarantine_log.sql")
    args = parser.parse_args()

    if args.run_migration:
        run_migration()
    elif args.list_quarantine:
        list_quarantine()
    else:
        result = process_file(args.file)
        sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
