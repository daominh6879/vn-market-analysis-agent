"""
data/snapshots.py — daily snapshots of series providers only expose "as of now"
(SJC gold, SBV central rate, VCB USD) so "so với hôm qua" comes from our own history.

All functions are non-fatal: a DB problem returns None / 0 and never breaks a market answer.
"""

from __future__ import annotations

import sys
from datetime import date


def save_snapshot(series: str, *, buy: float | None = None, sell: float | None = None,
                  value: float | None = None, unit: str = "", source: str = "",
                  on: date | None = None) -> bool:
    """Upsert today's (or `on`) snapshot for `series`. Returns True when written."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO daily_snapshots (snapshot_date, series, buy, sell, value, unit, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_date, series) DO UPDATE SET
                        buy = EXCLUDED.buy, sell = EXCLUDED.sell, value = EXCLUDED.value,
                        unit = EXCLUDED.unit, source = EXCLUDED.source, fetched_at = NOW()
                    """,
                    (on or date.today(), series, buy, sell, value, unit, source),
                )
        return True
    except Exception as exc:
        sys.stderr.write(f"[snapshots] save {series} failed: {exc}\n")
        return False


def previous_snapshot(series: str, before: date | None = None) -> dict | None:
    """Latest snapshot strictly before `before` (default today): {date, buy, sell, value}."""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_date, buy, sell, value FROM daily_snapshots "
                    "WHERE series = %s AND snapshot_date < %s ORDER BY snapshot_date DESC LIMIT 1",
                    (series, before or date.today()),
                )
                r = cur.fetchone()
        if not r:
            return None
        f = lambda v: float(v) if v is not None else None
        return {"date": r[0], "buy": f(r[1]), "sell": f(r[2]), "value": f(r[3])}
    except Exception:
        return None


def change_note(series: str, *, buy: float | None = None, sell: float | None = None,
                value: float | None = None, unit: str = "", decimals: int = 1) -> str:
    """"So với {date}: mua +0.5, bán +0.3 {unit}" or an honest 'no history yet' note."""
    prev = previous_snapshot(series)
    if not prev:
        return "Chưa có dữ liệu lịch sử các ngày trước để so sánh (hệ thống mới bắt đầu lưu snapshot hằng ngày)."
    parts = []
    for label, now, before in (("mua", buy, prev["buy"]), ("bán", sell, prev["sell"]), ("giá trị", value, prev["value"])):
        if now is not None and before is not None:
            d = now - before
            pct = d / before * 100 if before else 0.0
            parts.append(f"{label} {d:+,.{decimals}f} ({pct:+.2f}%)")
    if not parts:
        return "Chưa có dữ liệu lịch sử các ngày trước để so sánh."
    return f"So với snapshot {prev['date']}: " + ", ".join(parts) + (f" {unit}" if unit else "")
