"""Shared helpers for intent modules."""

from __future__ import annotations

import re

from llm.utils import strip_thinking  # noqa: F401  (re-exported for intent modules)


def extract_report(text: str) -> str:
    """Extract content inside <report>...</report> fence if the LLM used it."""
    m = re.search(r"<report>(.*?)</report>", text, re.DOTALL)
    return m.group(1).strip() if m else text


def strip_preamble(text: str) -> str:
    """Remove LLM meta-commentary before the first markdown heading."""
    m = re.search(r"^#", text, re.MULTILINE)
    if m:
        return text[m.start():]
    return text
