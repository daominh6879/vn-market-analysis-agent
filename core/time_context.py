"""
core/time_context.py — resolve a user question to an exact time window.

Every financial turn resolves the question against today's date so data retrieval is
always anchored to "now":

  - no date phrase           → anchor "now" (latest available data)
  - relative phrase          → exact [start_date, end_date] rolling window
    ("tuần trước", "tháng trước", "hôm qua", "năm ngoái", "last week", ...)
  - explicit date            → preserved as start/end

Resolution is LLM-assisted (one small create_client() call) with a deterministic regex
fallback so a broken/empty LLM response never blocks a turn.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from llm.factory import create_client
from llm.types import Message


@dataclass
class TimeContext:
    anchor: str            # ISO "YYYY-MM-DD" of `today` — the "now" anchor
    start_date: str | None  # None = "latest available / as of now"
    end_date: str | None    # default = anchor
    explicit: bool          # True when the question names a period beyond "now"


_TIME_SYSTEM = (
    "Bạn là bộ phân tích thời gian cho câu hỏi tài chính tiếng Việt. "
    f"Hôm nay là {date.today().isoformat()}. "
    "Xác định khoảng thời gian mà câu hỏi người dùng ngụ ý, quy về ngày hôm nay. "
    "Nếu câu hỏi không nêu mốc/khoảng thời gian (chỉ 'hiện tại', 'bây giờ', 'gần nhất', 'mới nhất') "
    "thì start_date = null và explicit = false (dùng dữ liệu mới nhất). "
    "Nếu câu hỏi nêu khoảng tương đối ('tuần trước', 'tháng trước', 'hôm qua', 'năm ngoái', "
    "'N ngày/tháng gần nhất', 'last week', 'last month') thì đổi thành khoảng ngày cụ thể: "
    "tuần = 7 ngày, tháng = 30 ngày, quý = 90 ngày, năm = 365 ngày, 'hôm qua' = 1 ngày. "
    "end_date luôn = hôm nay trừ khi câu hỏi nêu ngày kết thúc cụ thể."
)

_TIME_TOOL = {
    "name": "resolve_time",
    "description": "Return the exact time window the question implies, relative to today.",
    "input_schema": {
        "type": "object",
        "properties": {
            "start_date": {
                "type": ["string", "null"],
                "description": "ISO start date YYYY-MM-DD, or null for 'now/latest'.",
            },
            "end_date": {
                "type": ["string", "null"],
                "description": "ISO end date YYYY-MM-DD (default today).",
            },
            "explicit": {
                "type": "boolean",
                "description": "true when the question names a period beyond 'now'.",
            },
        },
        "required": ["start_date", "end_date", "explicit"],
    },
}


def _now_default(today: date) -> TimeContext:
    return TimeContext(
        anchor=today.isoformat(),
        start_date=None,
        end_date=today.isoformat(),
        explicit=False,
    )


# ── Deterministic fallback ─────────────────────────────────────────────────────

_NOW_WORDS = (
    "hôm nay", "hom nay", "hiện tại", "hiện nay", "bây giờ", "lúc này", "thời điểm này",
    "gần nhất", "mới nhất", "gần đây", "hiện thời", "today", "now", "current", "latest",
)

_UNIT_DAYS = {
    "ngày": 1, "ngay": 1, "day": 1, "days": 1,
    "tuần": 7, "tuan": 7, "week": 7, "weeks": 7,
    "tháng": 30, "thang": 30, "month": 30, "months": 30,
    "quý": 90, "quy": 90, "quarter": 90,
    "năm": 365, "nam": 365, "year": 365, "years": 365,
}

_FIXED_PHRASES: dict[str, int] = {
    "hôm qua": 1, "hom qua": 1, "yesterday": 1,
    "tuần trước": 7, "tuần qua": 7, "tuan truoc": 7, "last week": 7,
    "tháng trước": 30, "tháng qua": 30, "thang truoc": 30, "last month": 30,
    "quý trước": 90, "quy truoc": 90, "last quarter": 90,
    "năm ngoái": 365, "năm trước": 365, "nam ngoai": 365, "nam truoc": 365, "last year": 365,
}

_NUM_RE = re.compile(r"(\d+)\s*(ngày|ngay|tuần|tuan|tháng|thang|quý|quy|năm|nam|day|days|week|weeks|month|months|year|years)")


def _fallback(query: str, today: date) -> TimeContext:
    """Regex/VN+EN phrase fallback — used when the LLM call fails or returns nothing."""
    q = query.lower()
    # Numeric first: "6 tháng qua" must be 6*30d, not the fixed "tháng qua" (30d).
    m = _NUM_RE.search(q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = _UNIT_DAYS.get(unit, 30) * n
        start = (today - timedelta(days=days)).isoformat()
        return TimeContext(
            anchor=today.isoformat(),
            start_date=start,
            end_date=today.isoformat(),
            explicit=True,
        )
    for phrase, days in _FIXED_PHRASES.items():
        if phrase in q:
            start = (today - timedelta(days=days)).isoformat()
            return TimeContext(
                anchor=today.isoformat(),
                start_date=start,
                end_date=today.isoformat(),
                explicit=True,
            )
    if any(w in q for w in _NOW_WORDS):
        return _now_default(today)
    # No recognizable time phrase at all → "now" (latest available).
    return _now_default(today)


def _to_iso(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "null":
        return None
    return s[:10]  # tolerate datetime/timestamp → date part


def resolve_time_context(query: str, today: date | None = None) -> TimeContext:
    """Resolve a question to an exact time window. Never raises — always returns a TimeContext."""
    if today is None:
        today = date.today()
    q = (query or "").strip()
    if not q:
        return _now_default(today)

    try:
        client = create_client()
        resp = client.generate(
            messages=[Message(role="user", content=q)],
            system=_TIME_SYSTEM,
            tools=[_TIME_TOOL],
            max_tokens=256,
            temperature=0,
        )
        if resp.tool_calls:
            inp = resp.tool_calls[0].input or {}
            start = _to_iso(inp.get("start_date"))
            end = _to_iso(inp.get("end_date")) or today.isoformat()
            explicit = bool(inp.get("explicit", False))
            return TimeContext(
                anchor=today.isoformat(),
                start_date=start,
                end_date=end,
                explicit=explicit,
            )
    except Exception:
        pass
    return _fallback(q, today)


# ── Caller helpers ─────────────────────────────────────────────────────────────

def to_days(start: str | None, end: str | None, default: int = 7) -> int:
    """Convert a resolved range to a `days` count for tools that only accept `days`."""
    if not start or not end:
        return default
    try:
        d = (date.fromisoformat(end) - date.fromisoformat(start)).days
        return max(1, d)
    except Exception:
        return default


def to_dict(tc: TimeContext) -> dict:
    return asdict(tc)
