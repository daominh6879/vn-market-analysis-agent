# Re-export từ core.db — giữ backward compatibility cho ingest/ và core/quality.py
from core.db import get_conn, run_migration

__all__ = ["get_conn", "run_migration"]
