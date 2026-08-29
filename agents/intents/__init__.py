"""Shared helpers for intent modules."""

from __future__ import annotations

import re

# Lines that look like inline chain-of-thought from deepseek-chat.
_THINKING_LINE_RE = re.compile(
    r"^(Need|Let'?s|Hmm|Wait|Actually|Okay|Ok,|So,|But|Also,|Now|I need|I should|"
    r"Let me|Note:|Note that|This|Here|For|Maybe|Perhaps|However|Although|Since|"
    r"Looking|Based|Given|Checking|Could|Should|Would|We need|We have|We can|"
    r"Alright|Right,|Indeed|Remember|Recall|First,|Second,|Third,|Finally,|"
    r"Nên|Cần|Hãy|Thực ra|Thật ra|Để|Vậy|Nhưng|Có thể|Có lẽ|Tuy nhiên|"
    r"Dựa|Theo|Kiểm tra|Xem|Nhớ|Nhận|Đây|Bây giờ|Tiếp theo|"
    r"Chúng ta|Chúng tôi|Như vậy|Vậy là|Báo cáo đã|Đã đáp ứng|"
    r"Tôi cần|Tôi sẽ|Tôi nghĩ|Mình cần|Mình sẽ)",
    re.IGNORECASE,
)

# Parenthetical meta-commentary pattern: (Cần kiểm chứng...), (Note:...), (Lưu ý:...)
_PARENS_META_RE = re.compile(
    r"\s*\([^)]{0,200}(kiểm chứng|lưu ý|note|cần xác nhận|verify|check|todo|tbd|"
    r"chú ý|xem lại|cần bổ sung|nguồn chưa|chưa xác nhận)[^)]{0,200}\)",
    re.IGNORECASE,
)


def strip_preamble(text: str) -> str:
    """Remove LLM meta-commentary before the first markdown heading."""
    m = re.search(r"^#", text, re.MULTILINE)
    if m:
        return text[m.start():]
    return text


def strip_thinking(text: str) -> str:
    """Remove inline chain-of-thought that deepseek-chat leaks into output.

    - Strips <think>...</think> blocks
    - Removes lines starting with reasoning patterns
    - Strips parenthetical meta-commentary inline
    - Deduplicates: if same H1 heading appears twice, keep LAST occurrence (final draft wins)
    """
    # 1. Strip explicit think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Strip parenthetical meta-commentary inline
    text = _PARENS_META_RE.sub("", text)

    # 3. Line-level filtering
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        # Keep markdown structure lines — but still drop headings that are chain-of-thought
        if stripped.startswith(("#",)):
            # Strip leading '#' symbols and whitespace to check if it's a thinking line
            heading_text = stripped.lstrip("#").strip()
            if _THINKING_LINE_RE.match(heading_text):
                continue  # e.g. "# Let me reconsider..." → drop
            cleaned.append(line)
            continue
        if stripped.startswith(("-", "*", "|", ">", "```", "[")):
            cleaned.append(line)
            continue
        if _THINKING_LINE_RE.match(stripped):
            continue  # drop reasoning line
        cleaned.append(line)

    # 4. Dedup: if H1 heading appears more than once, keep last occurrence onward
    joined = "\n".join(cleaned)
    h1_matches = list(re.finditer(r"^# .+", joined, re.MULTILINE))
    if len(h1_matches) > 1:
        # Keep from last H1 heading
        joined = joined[h1_matches[-1].start():]

    # 5. Collapse 3+ blank lines
    result = re.sub(r"\n{3,}", "\n\n", joined)
    return result.strip()
