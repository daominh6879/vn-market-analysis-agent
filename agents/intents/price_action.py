"""
agents/intents/price_action.py — Nhóm 1: Hành động giá & Dòng tiền.

Market-brief pattern:
  - Python computes all data (price change, volume vs MA20, foreign flow).
  - LLM writes only 3 prose slots (GIA_BIEN_DONG, DONG_TIEN, KET_LUAN).
  - Python assembles final Markdown from fixed structure + LLM slots.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from tools.price import get_realtime_price, get_historical_ohlcv, get_foreign_flows
from agents.intents import strip_preamble, strip_thinking, extract_slot


_SYSTEM = (
    "Bạn là chuyên gia phân tích hành động giá chứng khoán Việt Nam. "
    "KHÔNG tự bịa số liệu — dùng đúng các số đã cung cấp. "
    "TUYỆT ĐỐI không viết quá trình suy nghĩ, không ghi chú nội bộ, "
    "không giải thích bước phân tích. "
    "Viết HOÀN TOÀN bằng tiếng Việt. "
    "Output chỉ gồm 3 phần được đánh dấu, không có text nào khác. "
    "BẮT BUỘC bọc toàn bộ output trong thẻ <report>...</report>. Output chỉ gồm: <report>[nội dung]</report>, không có text nào khác."
)


def _bn(value: float, signed: bool = False) -> str:
    """tỷ đồng with enough precision: per-ticker flows are often < 1 tỷ (0.06 tỷ = 60 triệu)."""
    sign = "+" if signed else ""
    if abs(value) >= 10:
        return f"{value:{sign},.0f}"
    if abs(value) >= 0.1:
        return f"{value:{sign},.1f}"
    return f"{value:{sign}.2f}"


def _get_foreign_flow_summary(ticker: str, as_of: str | None = None, live_fallback: bool = True) -> str:
    """Market-level flow for indices; per-ticker flow for stocks."""
    t = ticker.strip().upper()
    # Market indices → use market-level foreign flows
    if t in frozenset({"VNINDEX", "VN30", "HNX", "HNX30", "UPCOM"}):
        try:
            r = get_foreign_flows(days=1)
            if r.status == "ok":
                return r.message
        except Exception:
            pass
        return "Không có dữ liệu dòng tiền khối ngoại thị trường."
    # Stock ticker → per-ticker DB query (fresh) → live VCI fallback
    try:
        from datetime import date
        from tools.foreign_flow_db import query_ticker_foreign_net
        from tools.price import _is_db_fresh
        today = as_of or date.today().isoformat()
        row = query_ticker_foreign_net(ticker, today)
        if row and (not live_fallback or _is_db_fresh(row["date"])):
            # foreign_flows stores values in tỷ đồng (ingest divides raw VND by 1e9)
            session = "" if live_fallback else " (phiên " + str(row["date"])[:10] + ")"
            return (
                f"Khối ngoại{session}: mua {_bn(row['buy_value'])} tỷ, "
                f"bán {_bn(row['sell_value'])} tỷ, "
                f"ròng {_bn(row['net_value'], signed=True)} tỷ"
            )
    except Exception:
        pass
    if not live_fallback:  # historical date: live data would be today's, not the asked day's
        return "Không có dữ liệu khối ngoại cho ngày này."
    # Live fallback: VCI price board per-ticker foreign data (DB empty/stale)
    try:
        from tools.providers import VciDirectProvider
        rows = VciDirectProvider().fetch_foreign_batch([t])
        if rows:
            r = rows[0]
            return (
                f"Khối ngoại (live): mua {_bn(r['buy_value'] / 1e9)} tỷ, "
                f"bán {_bn(r['sell_value'] / 1e9)} tỷ, "
                f"ròng {_bn(r['net_value'] / 1e9, signed=True)} tỷ"
            )
    except Exception:
        pass
    return "Không có dữ liệu dòng tiền khối ngoại."


def _volume_vs_ma(df: pd.DataFrame, ma_window: int = 20) -> str:
    if df.empty or "volume" not in df.columns or len(df) < ma_window:
        return "Không đủ dữ liệu khối lượng."
    recent_vol = df["volume"].iloc[-1]
    ma_vol = df["volume"].tail(ma_window).mean()
    ratio = recent_vol / ma_vol if ma_vol > 0 else 0
    flag = "⚡ ĐỘT BIẾN" if ratio > 1.5 else ("Bình thường" if ratio >= 0.7 else "Thấp")
    return (
        f"Khối lượng phiên gần nhất: {recent_vol:,.0f} cp "
        f"({ratio:.1%} so với MA{ma_window}) — {flag}"
    )


def _price_change_summary(df: pd.DataFrame, ticker: str = "") -> str:
    if df.empty or "close" not in df.columns or len(df) < 2:
        return "Không đủ dữ liệu giá."
    last = df["close"].iloc[-1]
    prev = df["close"].iloc[-2]
    change_pct = (last - prev) / prev * 100 if prev > 0 else 0
    direction = "tăng" if change_pct > 0 else ("giảm" if change_pct < 0 else "không đổi")
    alert = " ⚠️ Biến động mạnh" if abs(change_pct) > 3 else ""
    # Today's bar before the 15:00 close is still moving - don't call it a closing price.
    from datetime import datetime
    in_session = ("time" in df.columns
                  and str(df["time"].iloc[-1])[:10] == datetime.now().date().isoformat()
                  and datetime.now().hour < 15)
    if ticker.strip().upper() in _INDEX_CODES:
        label = "Điểm hiện tại (đang giao dịch)" if in_session else "Điểm đóng cửa"
        return f"{label}: {last:,.2f} điểm ({direction} {abs(change_pct):.2f}%){alert}"
    label = "Giá hiện tại (đang giao dịch)" if in_session else "Giá đóng cửa"
    return f"{label}: {last:,.0f} VND ({direction} {abs(change_pct):.2f}%){alert}"


_INDEX_CODES = frozenset({"VNINDEX", "VN30", "HNX", "HNX30", "UPCOM"})


def _load_ohlcv(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    default_days: int = 25,
) -> pd.DataFrame | None:
    """DB-first for indices (market_index_daily); live API for stocks; CSV cache fallback."""
    t = ticker.strip().upper()
    from core.time_context import to_days
    days = to_days(start_date, end_date, default=default_days) if (start_date or end_date) else default_days

    # Index path: DB → live API (skip stale DB)
    if t in _INDEX_CODES:
        from tools.index_db import query_index
        from tools.price import _is_db_fresh
        df = query_index(t, days=days)
        if df is not None and not df.empty and _is_db_fresh(df["time"].iloc[-1]):
            # Rename matched_volume → volume for downstream compatibility
            if "matched_volume" in df.columns and "volume" not in df.columns:
                df = df.rename(columns={"matched_volume": "volume"})
            return df
        # Fallback: live VCI (DB empty or stale)
        r = get_historical_ohlcv(t, days=days, start_date=start_date, end_date=end_date)
        if r.status == "ok" and r.data is not None:
            return r.data
        return None

    # Stock path: live API → CSV cache
    r = get_historical_ohlcv(t, days=days, start_date=start_date, end_date=end_date)
    if r.status == "ok" and r.data is not None:
        return r.data
    cache = Path("outputs/agent_cache") / f"{t}_ohlcv.csv"
    if cache.exists():
        try:
            return pd.read_csv(cache)
        except Exception:
            pass
    return None


def _assemble_report(ticker: str, gia_bien_dong: str, dong_tien: str, ket_luan: str) -> str:
    return (
        f"# Hành động giá {ticker}\n\n"
        f"## Giá & Biến động\n{gia_bien_dong}\n\n"
        f"## Dòng tiền & Khối lượng\n{dong_tien}\n\n"
        f"## Kết luận ngắn\n{ket_luan}\n\n"
        f"[Nguồn: VCI REST API / DB]"
    )


def gather_data(ticker: str, query: str, time_context: dict | None = None) -> str:
    """Fetch price, OHLCV, foreign flow — no LLM call."""
    tc = time_context or {}
    explicit = bool(tc.get("explicit"))
    start = tc.get("start_date") if explicit else None
    end = tc.get("end_date") if explicit else None
    if start:
        # Pull a week of lead-in so the asked day has a previous close (change %, session table).
        from datetime import date as _d, timedelta as _td
        try:
            start = (_d.fromisoformat(str(start)[:10]) - _td(days=7)).isoformat()
        except ValueError:
            pass
    from datetime import date as _today_d
    historical = bool(end) and str(end)[:10] < _today_d.today().isoformat()
    price_r = None if historical else get_realtime_price(ticker)
    n_sessions = _session_count(query)
    # ~1.6 calendar days per session (weekends/holidays) + a buffer for the previous-close row.
    days = max(25, int((n_sessions or 0) * 1.6) + 5)
    df = _load_ohlcv(ticker, start_date=start, end_date=end, default_days=days)
    change_line = _price_change_summary(df, ticker) if df is not None else "Không có dữ liệu OHLCV."
    # One price per answer: when the OHLCV frame is available it already carries the latest
    # bar (incl. today's), so the separate live-price line would only risk a second, different
    # number from another cache. Keep it as the fallback when there is no frame.
    has_bars = df is not None and not df.empty and "close" in df.columns
    price_line  = (price_r.message + "\n") if price_r and not has_bars else ""
    if historical and df is not None and not df.empty and "time" in df.columns:
        change_line = f"Phiên {str(df['time'].iloc[-1])[:10]}: {change_line}"
    vol_line    = _volume_vs_ma(df)         if df is not None else "Không có dữ liệu khối lượng."
    flow_line   = _get_foreign_flow_summary(ticker, as_of=end, live_fallback=not historical)
    is_index = ticker.strip().upper() in _INDEX_CODES
    extras: list[str] = []
    if n_sessions and df is not None:
        table = _session_table(df, n_sessions, is_index)
        if table:
            extras.append(table)
    if df is not None and not df.empty and "close" in df.columns:
        if not is_index and not historical:
            cap = _market_cap_line(ticker.strip().upper(), float(df["close"].iloc[-1]))
            if cap:
                extras.append(cap)
        if "time" in df.columns:
            extras.append(f"Dữ liệu tính đến phiên {str(df['time'].iloc[-1])[:10]}.")
    tail = ("\n" + "\n".join(extras)) if extras else ""
    return (
        f"[GIÁ & DÒNG TIỀN {ticker}]\n"
        f"{price_line}"
        f"{change_line}\n"
        f"{vol_line}\n"
        f"{flow_line}"
        f"{tail}"
    )


_SESSIONS_RE = re.compile(r"(\d{1,2})\s*phiên", re.IGNORECASE)
_HISTORY_HINT_RE = re.compile(
    r"phiên|lịch sử giá|diễn biến giá|hôm qua|so với hôm qua|tuần qua|tháng qua", re.IGNORECASE
)
_MAX_SESSIONS = 30


def _session_count(query: str) -> int | None:
    """Number of sessions to tabulate: "5 phiên" -> 5, "hôm qua" -> 2, history hint -> 5, else None."""
    q = query or ""
    m = _SESSIONS_RE.search(q)
    if m:
        return max(2, min(int(m.group(1)), _MAX_SESSIONS))
    if "hôm qua" in q.lower():
        return 2
    if "tuần qua" in q.lower():
        return 5
    if "tháng qua" in q.lower():
        return 22
    if _HISTORY_HINT_RE.search(q):
        return 5
    return None


def _session_table(df: pd.DataFrame, n: int, is_index: bool) -> str:
    """Last n sessions: date, close, change vs previous session, volume."""
    if df is None or df.empty or "close" not in df.columns or "time" not in df.columns:
        return ""
    tail = df.tail(n + 1).reset_index(drop=True)
    unit = "điểm" if is_index else "VND"
    fmt = "{:,.2f}" if is_index else "{:,.0f}"
    rows = []
    for i in range(1, len(tail)):
        prev, cur = float(tail["close"].iloc[i - 1]), float(tail["close"].iloc[i])
        pct = (cur - prev) / prev * 100 if prev else 0.0
        vol = tail["volume"].iloc[i] if "volume" in tail.columns else None
        vol_s = f"{int(vol):,}" if vol is not None and pd.notna(vol) else "N/A"
        rows.append(f"| {str(tail['time'].iloc[i])[:10]} | {fmt.format(cur)} | {pct:+.2f}% | {vol_s} |")
    if not rows:
        return ""
    head = (f"Diễn biến {len(rows)} phiên gần nhất (giá đóng cửa, đơn vị {unit}):\n"
            "| Ngày | Đóng cửa | Thay đổi | KLGD |\n|---|---|---|---|\n")
    return head + "\n".join(rows)


def _market_cap_line(ticker: str, close_vnd: float | None) -> str:
    """Market cap = listed shares x last close (needs securities.listed_shares)."""
    if not close_vnd:
        return ""
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT listed_shares FROM securities WHERE ticker = %s", (ticker,))
                row = cur.fetchone()
    except Exception:
        return ""
    if not row or not row[0]:
        return ""
    shares = float(row[0])
    return (f"Vốn hóa ước tính: {shares * close_vnd / 1e9:,.0f} tỷ VND "
            f"({shares:,.0f} cp niêm yết × {close_vnd:,.0f} VND, giá mới nhất)")


@observe(name="intent.price_action")
def run(ticker: str, query: str) -> str:
    price_r = get_realtime_price(ticker)
    df = _load_ohlcv(ticker)

    price_line  = price_r.message
    vol_line    = "Không có dữ liệu OHLCV."
    change_line = "Không có dữ liệu OHLCV."
    if df is not None:
        vol_line    = _volume_vs_ma(df)
        change_line = _price_change_summary(df, ticker)
    flow_line = _get_foreign_flow_summary(ticker)

    user_prompt = f"""Dữ liệu thị trường {ticker}:
- {price_line}
- {change_line}
- {vol_line}
- {flow_line}

Logic:
- Giá tăng/giảm >3% VÀ khối lượng >150% MA20 → "Phiên có dòng tiền lớn (Breakout/Selloff)"
- Khối ngoại ròng dương → tín hiệu tích lũy; âm → phân phối

Câu hỏi: {query}

Bọc TOÀN BỘ output trong <report>...</report>.

<report>
GIA_BIEN_DONG: [2-3 câu về giá hiện tại, mức biến động, ý nghĩa]
DONG_TIEN: [2-3 câu về khối lượng vs MA20 và dòng tiền khối ngoại]
KET_LUAN: [1-2 câu kết luận: Breakout / Selloff / Tích lũy / Bình thường]
</report>"""

    client = create_client()
    resp = client.generate(
        [Message(role="user", content=user_prompt)],
        max_tokens=800,
        temperature=0,
        system=_SYSTEM,
    )

    from agents.intents import extract_report
    raw = extract_report(resp.text.strip())
    gia_bien_dong = strip_thinking(extract_slot(raw, "GIA_BIEN_DONG", "DONG_TIEN"))
    dong_tien     = strip_thinking(extract_slot(raw, "DONG_TIEN",     "KET_LUAN"))
    ket_luan      = strip_thinking(extract_slot(raw, "KET_LUAN",      None))

    if not gia_bien_dong and not dong_tien:
        return f"Không thể phân tích giá & dòng tiền **{ticker}** — vui lòng thử lại."

    return _assemble_report(ticker, gia_bien_dong, dong_tien, ket_luan)
