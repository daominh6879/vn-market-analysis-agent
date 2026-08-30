"""llm/utils.py — Post-processing helpers for raw LLM text output.

Kept in llm/ (not agents/) so clients can import without circular deps.
"""

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
    r"Tôi cần|Tôi sẽ|Tôi nghĩ|Mình cần|Mình sẽ|"
    r"Chỉ cần|Dữ liệu:|Điều kiện|Hãy đọc|Có lẽ giá|Kháng cự|Hỗ trợ:|"
    r"Giá hiện tại|Mốc tâm lý|Fibonacci|Swing|Phiên|Nhận định|"
    r"Điều đầu tiên|Yêu cầu cuối|người dùng yêu cầu|cung cấp dữ liệu|"
    r"Tuy nhiên,|Lưu ý:|Chú ý:|Ghi chú:|"
    r"Trước tiên,|Trước hết,|Đầu tiên,|Tiếp theo,|Cuối cùng,|"
    r"Tôi sẽ|Tôi cần|Tôi phải|Tôi có thể|Phải xem|Phải tính|"
    r"Ta cần|Ta phải|Ta sẽ|Ta có thể|Ta thấy|Ta xét|"
    r"[\U0001F300-\U0001FFFF])",
    re.IGNORECASE,
)

# Verbatim system-prompt echo — specific enough to never appear in a real report.
_SYSTEM_LEAK_RE = re.compile(
    r"TUYỆT ĐỐI KHÔNG viết|Xuất NGAY báo cáo|BẮT BUỘC có Entry|"
    r"Chỉ báo cáo cuối cùng|bắt đầu bằng.*# Phân tích|R:R phải ≥|"
    r"Setup chưa đủ hấp dẫn.*bắt buộc|Yêu cầu cuối:.*Markdown",
    re.IGNORECASE,
)

_PARENS_META_RE = re.compile(
    r"\s*\([^)]{0,200}(kiểm chứng|lưu ý|note|cần xác nhận|verify|check|todo|tbd|"
    r"chú ý|xem lại|cần bổ sung|nguồn chưa|chưa xác nhận)[^)]{0,200}\)",
    re.IGNORECASE,
)


def strip_thinking(text: str) -> str:
    """Remove inline chain-of-thought that deepseek-chat leaks into output."""
    # 1. Explicit think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Emoji-introduced thinking blocks (📈... until next ##)
    text = re.sub(
        r"(?m)^[\U0001F300-\U0001FFFF][^\n]*\n(?:(?!^##)[^\n]*\n)*",
        "",
        text,
    )

    # 3. Parenthetical meta-commentary
    text = _PARENS_META_RE.sub("", text)

    # 4. Line-level filtering
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if stripped.startswith(("#",)):
            heading_text = stripped.lstrip("#").strip()
            if _THINKING_LINE_RE.match(heading_text):
                continue
            cleaned.append(line)
            continue
        if stripped.startswith(("-", "*", "|", ">", "```", "[")):
            cleaned.append(line)
            continue
        if _THINKING_LINE_RE.match(stripped):
            continue
        if _SYSTEM_LEAK_RE.search(stripped):
            continue
        cleaned.append(line)

    # 5. Dedup: keep from last H1 when same heading appears twice (draft re-do pattern)
    joined = "\n".join(cleaned)
    h1_matches = list(re.finditer(r"^# .+", joined, re.MULTILINE))
    if len(h1_matches) > 1:
        joined = joined[h1_matches[-1].start():]

    return re.sub(r"\n{3,}", "\n\n", joined).strip()
