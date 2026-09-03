"""
tools/vn_holidays.py — VN stock-market holidays (HSX closed).

Single source of truth for holiday-aware freshness checks and the market-brief
holiday lookahead. Keys are YYYY-MM-DD.

Sources:
  - Nghị định 145/2013/NĐ-CP (official public holidays).
  - 2026 Quốc khánh 2/9: official holidays 1/9 and 2/9 only (Thông báo 9441/TB-BNV).
    31/8 and 3/9 are normal trading days — HSX does not follow the state-sector swap.
"""

from __future__ import annotations

VN_HOLIDAYS: dict[str, str] = {
    "2026-01-01": "Tết Dương lịch (1/1)",
    "2026-02-17": "Tết Nguyên Đán",
    "2026-02-18": "Tết Nguyên Đán",
    "2026-02-19": "Tết Nguyên Đán",
    "2026-02-20": "Tết Nguyên Đán",
    "2026-04-30": "Giải phóng miền Nam (30/4)",
    "2026-05-01": "Quốc tế Lao động (1/5)",
    "2026-09-01": "Quốc khánh 2/9",
    "2026-09-02": "Quốc khánh 2/9",
}


def is_vn_holiday(date_str: str) -> bool:
    """True if date_str (YYYY-MM-DD) is a VN stock-market holiday."""
    return date_str in VN_HOLIDAYS
