"""Shared helpers for intent modules."""

from __future__ import annotations

import re

from llm.utils import strip_thinking  # noqa: F401  (re-exported for intent modules)

# Append to every intent system prompt — mirrors market_brief pattern.
NO_THINKING_INSTR = (
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Không mở đầu bằng 'Được rồi', 'Tôi sẽ', 'Hãy', hay bất kỳ câu dẫn nào. "
    "Output chỉ gồm báo cáo thuần túy bọc trong <report>...</report>."
)


def extract_report(text: str) -> str:
    """Extract content inside <report>...</report> fence if the LLM used it."""
    m = re.search(r"<report>(.*?)</report>", text, re.DOTALL)
    return m.group(1).strip() if m else text


def extract_slot(text: str, label: str, next_label: str | None) -> str:
    """Extract text between last 'LABEL:' and the next 'NEXT_LABEL:' (or end).

    Uses the LAST occurrence so that reasoning preamble (which also mentions labels)
    is skipped in favour of the actual answer written at the end.
    Handles bold/spaced variants: **LABEL:** or LABEL : or LABEL:.
    """
    pattern = rf"(?:\*{{0,2}}){re.escape(label)}(?:\*{{0,2}})\s*:"
    matches = list(re.finditer(pattern, text))
    if not matches:
        return ""
    m = matches[-1]  # last = actual answer, not reasoning mention
    start = m.end()
    if next_label:
        nxt = re.search(rf"(?:\*{{0,2}}){re.escape(next_label)}(?:\*{{0,2}})\s*:", text[start:])
        end = start + nxt.start() if nxt else len(text)
    else:
        end = len(text)
    return text[start:end].strip()


def strip_preamble(text: str) -> str:
    """Remove LLM meta-commentary before the first H1 markdown heading."""
    # Match H1 only (single # followed by space) — not ## thinking headings
    m = re.search(r"^# [^\n#]", text, re.MULTILINE)
    if m:
        return text[m.start():]
    # Fallback: any heading
    m = re.search(r"^#", text, re.MULTILINE)
    if m:
        return text[m.start():]
    return text
