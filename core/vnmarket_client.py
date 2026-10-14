"""Shared vnmarket client (one instance: shared cache, throttle and source health)."""
from __future__ import annotations

import threading

from vnmarket import VnMarket

_client: VnMarket | None = None
_lock = threading.Lock()


def get_client() -> VnMarket:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = VnMarket()
    return _client
