"""
data/db.py — Postgres helper dùng trong Bài 10.

Kết nối đọc từ .env qua core/config.py.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings


def _dsn() -> str:
    return (
        f"host=127.0.0.1 port=5432 "
        f"dbname={settings.POSTGRES_DB} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )


@contextmanager
def get_conn():
    conn = psycopg2.connect(_dsn())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migration(sql_path: str) -> None:
    """Chạy file SQL migration."""
    sql = Path(sql_path).read_text(encoding="utf-8")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print(f"Migration done: {sql_path}")
