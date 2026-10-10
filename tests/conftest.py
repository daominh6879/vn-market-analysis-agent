"""
tests/conftest.py — Shared fixtures.

Tool-level cache (tools/cache.py) is process-wide in-memory. Unit tests swap
mocks with the same tool args, so a cached ToolResult from one test would leak
into the next. Clear it before/after every test to keep them isolated.
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

# Load .env so tests run in isolation see the same LLM/DB credentials as the app.
load_dotenv()


@pytest.fixture(autouse=True)
def _clear_tool_cache():
    from tools.cache import clear
    clear()
    yield
    clear()
