"""
tools/events_views.py — Query tools for Phase 5 data (Phase 5).

  get_corporate_events(ticker, days_ahead)  → upcoming GDKHQ / dividend events
  get_broker_views(ticker_or_index, days)   → recent CTCK price targets / levels
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from tools.result import ToolResult
from tracing import instrument_tool


@instrument_tool("get_corporate_events")
def get_corporate_events(
    ticker: Optional[str] = None,
    days_ahead: int = 7,
    days_back: int = 0,
) -> ToolResult:
    """
    Query upcoming corporate events from corporate_events table.

    Args:
        ticker:     filter by ticker; None = all tickers.
        days_ahead: window in calendar days from today (forward).
        days_back:  how many days before today to include (default 0 = today only as start).
                    Use days_back=1 to catch same-day ex_date events that may have been
                    stored with yesterday's date.

    Returns ToolResult with data as list[dict] and formatted message.
    """
    try:
        from core.db import get_conn

        today = date.today()
        from_date = today - timedelta(days=days_back)
        until = today + timedelta(days=days_ahead)

        params: list = [from_date, until]
        where = "ex_date BETWEEN %s AND %s"
        if ticker:
            where += " AND ticker = %s"
            params.append(ticker.upper())

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ticker, event_type, ex_date, record_date, ratio, note
                    FROM corporate_events
                    WHERE {where}
                    ORDER BY ex_date, ticker
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        if not rows:
            subject = f" cho {ticker}" if ticker else ""
            return ToolResult(
                status="no_data",
                data=[],
                message=f"Không có sự kiện quyền{subject} trong {days_ahead} ngày tới.",
            )

        lines: list[str] = []
        for r in rows:
            ratio_str = f" ({r['ratio']}%)" if r["ratio"] else ""
            lines.append(
                f"• {r['ticker']:6s} | {r['event_type']:12s} | {r['ex_date']}{ratio_str} — {(r['note'] or '')[:60]}"
            )

        return ToolResult(
            status="ok",
            data=rows,
            message="\n".join(lines),
        )

    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Lỗi truy vấn corporate_events: {e}",
        )


@instrument_tool("get_broker_views")
def get_broker_views(
    ticker_or_index: str = "VNINDEX",
    days: int = 7,
) -> ToolResult:
    """
    Query recent broker price targets and technical levels.

    Args:
        ticker_or_index: ticker ('HPG') or index name ('VNINDEX'). Case-insensitive.
        days:            look-back window in calendar days.

    Returns ToolResult with data as list[dict] sorted by published_at DESC.
    """
    try:
        from core.db import get_conn

        since = datetime.now(tz=timezone.utc) - timedelta(days=days)
        subject = ticker_or_index.upper().strip()

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT broker, ticker_or_index, published_at,
                           stance, target, support, resistance, source_url
                    FROM broker_views
                    WHERE ticker_or_index = %s
                      AND published_at >= %s
                    ORDER BY published_at DESC
                    LIMIT 20
                    """,
                    (subject, since),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        if not rows:
            return ToolResult(
                status="no_data",
                data=[],
                message=f"Không có nhận định CTCK cho {subject} trong {days} ngày qua.",
            )

        lines: list[str] = []
        for r in rows:
            parts = [f"{r['broker']:10s} →"]
            if r["stance"]:
                parts.append(r["stance"])
            if r["target"]:
                parts.append(f"target {r['target']:,.0f}")
            if r["support"]:
                parts.append(f"hỗ trợ {r['support']:,.0f}")
            if r["resistance"]:
                parts.append(f"kháng cự {r['resistance']:,.0f}")
            lines.append("• " + " | ".join(parts))

        return ToolResult(
            status="ok",
            data=rows,
            message="\n".join(lines),
        )

    except Exception as e:
        return ToolResult(
            status="upstream_error",
            data=None,
            message=f"Lỗi truy vấn broker_views: {e}",
        )
