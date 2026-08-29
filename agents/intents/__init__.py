"""Shared helpers for intent modules."""

from __future__ import annotations

import re


def strip_preamble(text: str) -> str:
    """Remove LLM meta-commentary that appears before the first markdown heading.

    DeepSeek occasionally outputs reasoning about the instructions before the
    actual report. Strip everything before the first line starting with '#'.
    """
    m = re.search(r"^#", text, re.MULTILINE)
    if m:
        return text[m.start():]
    return text
