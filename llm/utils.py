"""llm/utils.py — Post-processing helpers for raw LLM text output.

Kept in llm/ (not agents/) so clients can import without circular deps.
"""

from __future__ import annotations

import re

# Patterns for PLAIN LINES that are thinking (not used for heading check).
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
    r"Cấu trúc xu hướng|Entry:|SL:|TP1:|TP2:|R:R|"
    r"Nếu giá|Nếu chúng|Nếu entry|Nếu SL|Nếu TP|"
    r"Vậy entry|Vậy SL|Vậy TP|Vậy R:R|Vậy hỗ trợ|"
    r"Tuy nhiên nếu|Nhưng nếu|Nhưng thiếu|Nhưng họ|"
    r"RSI\(|MACD:|MA\d+\s*[=:]|EMA\d+|SMA\d+|ADX\(|"
    r"Bollinger|Stochastic|OBV:|Volume:|Vol\s*[=:]|"
    r"[\U0001F300-\U0001FFFF])",
    re.IGNORECASE,
)

# Patterns for HEADINGS that are thinking (narrower — avoids stripping report headings).
_HEADING_THINKING_RE = re.compile(
    r"^(Need|Let'?s|Hmm|Wait|Actually|I need|I should|Let me|We need|We have|We can|"
    r"Note:|Remember|Recall|Alright|Indeed|"
    r"Tôi cần|Tôi sẽ|Tôi phải|Mình cần|Mình sẽ|"
    r"Ta cần|Ta phải|Ta sẽ|Chúng ta|Chúng tôi|"
    r"Hãy xem|Để kiểm tra|Vậy là|Thực ra|"
    r"[\U0001F300-\U0001FFFF])",
    re.IGNORECASE,
)

# Verbatim system-prompt echo — specific enough to never appear in a real report.
_SYSTEM_LEAK_RE = re.compile(
    r"TUYỆT ĐỐI KHÔNG viết|Xuất NGAY báo cáo|BẮT BUỘC có Entry|"
    r"Chỉ báo cáo cuối cùng|bắt đầu bằng.*# Phân tích|R:R phải ≥|"
    r"Setup chưa đủ hấp dẫn.*bắt buộc|Yêu cầu cuối:.*Markdown|"
    r"Logic bắt buộc:|Bắt đầu NGAY bằng|KHÔNG có text nào ngoài|"
    r"Bọc toàn bộ báo cáo|Không được nói.*không đủ dữ liệu",
    re.IGNORECASE,
)

_PARENS_META_RE = re.compile(
    r"\s*\([^)]{0,200}(kiểm chứng|lưu ý|note|cần xác nhận|verify|check|todo|tbd|"
    r"chú ý|xem lại|cần bổ sung|nguồn chưa|chưa xác nhận)[^)]{0,200}\)",
    re.IGNORECASE,
)


def strip_thinking(text: str) -> str:
    """Remove inline chain-of-thought that deepseek-chat leaks into output."""
    # 0. Protect <report>...</report> — pull it out before any stripping, restore after.
    #    This guarantees report content is never touched regardless of what thinking
    #    patterns appear inside the report.
    _PLACEHOLDER = "\x00REPORT_BLOCK\x00"
    report_m = re.search(r"<report>.*?</report>", text, re.DOTALL)
    report_saved = report_m.group(0) if report_m else None
    if report_saved:
        text = text[: report_m.start()] + _PLACEHOLDER + text[report_m.end() :]

    # 1. Explicit think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Emoji-introduced thinking blocks (📈... until next # heading or placeholder)
    # Three parts: emoji line, zero-or-more middle lines (each ending \n),
    # optional last line without trailing \n (EOF case).
    # Stops at ANY # heading (H1, H2, H3...) so the report title is never consumed.
    text = re.sub(
        r"(?m)"
        r"^[\U0001F300-\U0001FFFF][^\n]*\n"   # emoji line
        r"(?:(?!^#)(?!\x00)[^\n]*\n)*"         # lines with \n (stop at any # or placeholder)
        r"(?:(?!^#)(?!\x00)[^\n]+)?",          # optional last line without \n
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
            if _HEADING_THINKING_RE.match(heading_text):
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

    result = re.sub(r"\n{3,}", "\n\n", joined).strip()

    # Restore protected <report> block
    if report_saved:
        result = result.replace(_PLACEHOLDER, report_saved)

    return result
