"""
agents/intents/market_data.py — market-wide factual questions (no single ticker).

Answers questions the market_brief template cannot: top gainers/losers, index close,
top foreign net buy/sell, top liquidity, top market cap, "list the tickers of sector X".

gather_data() only reads Postgres (ohlcv_daily / market_index_daily / foreign_flows /
securities) and returns labelled text — the shared synthesize_final step writes the answer.
"""

from __future__ import annotations

import re
import sys

from agents.intent_rules import detect_market_topics

_DEFAULT_N = 10
_MIN_VOLUME = 100_000         # drop thin prints (e.g. 10k-share +7% moves) from gainer/loser rankings
_THOUSAND_VND = 1000.0        # ohlcv_daily stores stock prices in nghìn đồng
_EXCHANGES = ("HOSE", "HNX", "UPCOM")
_INDEX_ALIASES = (
    ("hnx30", "HNX30"), ("hnx-index", "HNX"), ("hnxindex", "HNX"), ("hnx", "HNX"),
    ("upcom", "UPCOM"), ("vn30", "VN30"),
)


def _rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as exc:
        sys.stderr.write(f"[market_data] query failed: {exc}\n")
        return None


def _top_n(query: str, default: int = _DEFAULT_N) -> int:
    m = re.search(r"top\s*(\d{1,2})|(\d{1,2})\s*(mã|cổ phiếu|cp)\b", query or "", re.IGNORECASE)
    if m:
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= 30:
            return n
    return default


def _exchange(query: str) -> str | None:
    q = (query or "").upper()
    return next((e for e in _EXCHANGES if e in q), None)


_WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def _session_label(d) -> str:
    """One consistent label: "phiên giao dịch gần nhất (Thứ Sáu 09/10/2026)" - never a bare "phiên 10-10"."""
    from datetime import date as _date
    try:
        dd = d if isinstance(d, _date) else _date.fromisoformat(str(d)[:10])
        return f"phiên giao dịch gần nhất ({_WEEKDAYS[dd.weekday()]} {dd:%d/%m/%Y})"
    except ValueError:
        return f"phiên giao dịch gần nhất ({d})"


def _latest_sessions() -> tuple | None:
    """(latest_date, previous_date) from ohlcv_daily over active securities."""
    rows = _rows("SELECT DISTINCT date FROM ohlcv_daily ORDER BY date DESC LIMIT 2")
    if not rows or len(rows) < 2:
        return None
    return rows[0][0], rows[1][0]


# ── topic builders ────────────────────────────────────────────────────────────

def _movers(query: str) -> str:
    sessions = _latest_sessions()
    if not sessions:
        return "[TOP TĂNG/GIẢM]\nKhông có dữ liệu giá trong cơ sở dữ liệu."
    latest, prev = sessions
    exch = _exchange(query)
    sql = (
        "SELECT c.ticker, c.close, p.close, c.volume, s.exchange "
        "FROM ohlcv_daily c "
        "JOIN ohlcv_daily p ON p.ticker = c.ticker AND p.date = %s "
        "JOIN securities s ON s.ticker = c.ticker AND s.is_active "
        "WHERE c.date = %s AND p.close > 0 AND c.volume >= %s"
        + (" AND s.exchange = %s" if exch else "")
    )
    params = (prev, latest, _MIN_VOLUME) + ((exch,) if exch else ())
    rows = _rows(sql, params)
    if not rows:
        return "[TOP TĂNG/GIẢM]\nKhông có dữ liệu giá cho phiên gần nhất."
    items = [
        {"ticker": t, "close": float(c) * _THOUSAND_VND, "pct": (float(c) - float(p)) / float(p) * 100,
         "volume": int(v), "exchange": e}
        for t, c, p, v, e in rows
    ]
    n = _top_n(query)
    q = query.lower()
    want_up = "tăng" in q or "gainer" in q or "tăng trần" in q
    want_down = "giảm" in q or "loser" in q or "giảm sàn" in q
    if not want_up and not want_down:
        want_up = want_down = True
    scope = exch or "toàn thị trường (HOSE/HNX/UPCOM)"
    out = [f"[TOP TĂNG/GIẢM GIÁ — {_session_label(latest)}, {scope}, {len(items)} mã có KLGD ≥ {_MIN_VOLUME:,}]"]

    def fmt(rs):
        return "\n".join(
            f"{i}. {r['ticker']} ({r['exchange']}): {r['close']:,.0f} VND, {r['pct']:+.2f}%, KL {r['volume']:,} cp"
            for i, r in enumerate(rs, 1)
        )

    if want_up:
        out.append(f"Top {n} tăng mạnh nhất:\n" + fmt(sorted(items, key=lambda x: -x["pct"])[:n]))
    if want_down:
        out.append(f"Top {n} giảm mạnh nhất:\n" + fmt(sorted(items, key=lambda x: x["pct"])[:n]))
    adv = sum(1 for i in items if i["pct"] > 0)
    dec = sum(1 for i in items if i["pct"] < 0)
    out.append(f"Độ rộng: {adv} mã tăng / {len(items) - adv - dec} đứng giá / {dec} mã giảm.")
    return "\n".join(out)


def _index(query: str) -> str:
    code = next((c for kw, c in _INDEX_ALIASES if kw in (query or "").lower()), "VNINDEX")
    try:
        from tools.index_db import query_index_latest
        r = query_index_latest(code)
    except Exception:
        r = None
    if not r:
        return f"[CHỈ SỐ {code}]\nKhông có dữ liệu chỉ số {code} trong cơ sở dữ liệu."
    val = f"{r['matched_value'] / 1e9:,.0f} tỷ" if r.get("matched_value") is not None else "N/A"
    fnet = f"{r['foreign_net']:+,.0f} tỷ" if r.get("foreign_net") is not None else "N/A"
    return (
        f"[CHỈ SỐ {code} — {_session_label(r['date'])}]\n"
        f"Đóng cửa: {r['close']:,.2f} điểm ({r['change_pts']:+,.2f} điểm, {r['change_pct']:+.2f}%)\n"
        f"Thanh khoản: {val}; KLGD khớp lệnh: {r['matched_volume']:,} cp; khối ngoại ròng: {fnet}"
    )


def _foreign(query: str) -> str:
    try:
        from tools.foreign_flow_db import (
            query_latest_foreign_date, query_market_foreign_net, query_top_foreign,
        )
        d = query_latest_foreign_date()
        if not d:
            return "[KHỐI NGOẠI]\nKhông có dữ liệu giao dịch khối ngoại."
        market = query_market_foreign_net(d)
        n = _top_n(query, 5)
        q = query.lower()
        want_buy = "mua" in q or "buy" in q
        want_sell = "bán" in q or "sell" in q
        if not want_buy and not want_sell:
            want_buy = want_sell = True
        out = [f"[KHỐI NGOẠI — {_session_label(d)}]"]
        if market:
            out.append(
                f"Toàn thị trường: mua {market['total_buy']:,.0f} tỷ, bán {market['total_sell']:,.0f} tỷ, "
                f"ròng {market['net_value']:+,.0f} tỷ"
            )

        def fmt(rows):
            return "\n".join(
                f"{i}. {r['ticker']}: ròng {r['net_value']:+,.1f} tỷ (mua {r['buy_value']:,.1f}, bán {r['sell_value']:,.1f})"
                for i, r in enumerate(rows, 1)
            )

        # Rank organic (order-matching) flow only; negotiated block deals are listed separately
        # so one thỏa thuận print (e.g. a -9,593 tỷ block) is not read as market-wide selling.
        blocks: dict[str, dict] = {}

        def organic(direction: str) -> list[dict]:
            rows = query_top_foreign(d, n * 4, direction) or []
            for r in rows:
                if r.get("block_trade"):
                    blocks[r["ticker"]] = r
            return [r for r in rows if not r.get("block_trade")][:n]

        if want_buy:
            out.append(f"Top {n} mua ròng nhiều nhất (khớp lệnh, đã loại giao dịch thỏa thuận):\n"
                       + (fmt(organic("net_buy")) or "(không có)"))
        if want_sell:
            out.append(f"Top {n} bán ròng nhiều nhất (khớp lệnh, đã loại giao dịch thỏa thuận):\n"
                       + (fmt(organic("net_sell")) or "(không có)"))
        if blocks:
            top_blocks = sorted(blocks.items(), key=lambda kv: -abs(kv[1]["net_value"]))[:5]
            out.append("Giao dịch thỏa thuận lớn của khối ngoại (không tính vào xếp hạng trên): "
                       + "; ".join(f"{t} ròng {r['net_value']:+,.1f} tỷ" for t, r in top_blocks))
        return "\n".join(out)
    except Exception as exc:
        return f"[KHỐI NGOẠI]\nLỗi truy vấn: {exc}"


def _liquidity(query: str) -> str:
    sessions = _latest_sessions()
    if not sessions:
        return "[THANH KHOẢN]\nKhông có dữ liệu giá."
    latest, prev = sessions
    n = _top_n(query)
    exch = _exchange(query)
    rows = _rows(
        "SELECT c.ticker, c.close, p.close, c.volume, c.close * c.volume * 1000 / 1e9 AS val, s.exchange "
        "FROM ohlcv_daily c "
        "LEFT JOIN ohlcv_daily p ON p.ticker = c.ticker AND p.date = %s "
        "JOIN securities s ON s.ticker = c.ticker AND s.is_active "
        "WHERE c.date = %s" + (" AND s.exchange = %s" if exch else "") +
        " ORDER BY val DESC LIMIT %s",
        (prev, latest) + ((exch,) if exch else ()) + (n,),
    )
    if not rows:
        return "[THANH KHOẢN]\nKhông có dữ liệu."
    lines = []
    for i, (t, c, p, v, val, e) in enumerate(rows, 1):
        pct = f", {(float(c) - float(p)) / float(p) * 100:+.2f}%" if p else ""
        lines.append(f"{i}. {t} ({e}): GTGD ~{float(val):,.0f} tỷ, KL {int(v):,} cp, giá {float(c) * _THOUSAND_VND:,.0f} VND{pct}")
    return f"[TOP {n} THANH KHOẢN — {_session_label(latest)}]\n" + "\n".join(lines)


def _marketcap_coverage_note(exch: str | None) -> str:
    """Warn when some active tickers have no share count (they are silently absent from the ranking)."""
    r = _rows(
        "SELECT COUNT(*) FILTER (WHERE COALESCE(listed_shares, 0) = 0), COUNT(*) FROM securities "
        "WHERE is_active" + (" AND exchange = %s" if exch else ""),
        ((exch,) if exch else ()),
    )
    if r and r[0][0]:
        return f"\nLưu ý: {r[0][0]}/{r[0][1]} mã chưa có số cổ phiếu niêm yết nên không nằm trong xếp hạng."
    return ""


def _marketcap(query: str) -> str:
    sessions = _latest_sessions()
    if not sessions:
        return "[VỐN HÓA]\nKhông có dữ liệu giá."
    latest, _ = sessions
    n = _top_n(query, 5)
    exch = _exchange(query)
    rows = _rows(
        "SELECT s.ticker, s.company_name, s.exchange, s.listed_shares, c.close, "
        "s.listed_shares * c.close * 1000 / 1e9 AS mcap "
        "FROM securities s JOIN ohlcv_daily c ON c.ticker = s.ticker AND c.date = %s "
        "WHERE s.is_active AND s.listed_shares > 0" + (" AND s.exchange = %s" if exch else "") +
        " ORDER BY mcap DESC LIMIT %s",
        (latest,) + ((exch,) if exch else ()) + (n,),
    )
    if not rows:
        return "[VỐN HÓA]\nKhông có dữ liệu số cổ phiếu niêm yết để tính vốn hóa."
    lines = [
        f"{i}. {t} — {name}: ~{float(m):,.0f} tỷ VND (giá {float(c) * _THOUSAND_VND:,.0f} × {int(sh):,} cp niêm yết)"
        for i, (t, name, e, sh, c, m) in enumerate(rows, 1)
    ]
    scope = exch or "toàn thị trường"
    return (
        f"[TOP {n} VỐN HÓA — {scope}, giá đóng cửa {_session_label(latest)}]\n" + "\n".join(lines) +
        "\nVốn hóa = số cổ phiếu niêm yết × giá đóng cửa gần nhất." + _marketcap_coverage_note(exch)
    )


def _list_sector(query: str) -> str:
    from agents.focus import extract_focus_sector
    from core.tickers import get_sector_tickers
    label = extract_focus_sector(query)
    if not label:
        return "[DANH SÁCH MÃ]\nKhông xác định được ngành cần liệt kê."
    tickers = get_sector_tickers(label)
    if not tickers:
        return f"[DANH SÁCH MÃ]\nKhông có mã nào thuộc ngành '{label}'."
    rows = _rows(
        "SELECT ticker, company_name, exchange FROM securities WHERE ticker = ANY(%s) ORDER BY exchange, ticker",
        (tickers,),
    ) or [(t, "", "") for t in tickers]
    lines = [f"- {t} ({e}) {name}".rstrip() for t, name, e in rows]
    return f"[DANH SÁCH MÃ NGÀNH {label.upper()} — {len(rows)} mã]\n" + "\n".join(lines)


_BUILDERS = {
    "movers": _movers,
    "index": _index,
    "foreign": _foreign,
    "liquidity": _liquidity,
    "marketcap": _marketcap,
    "list": _list_sector,
}


def gather_data(ticker: str | None, query: str, time_context: dict | None = None,
                topics: list[str] | None = None) -> str:
    """Return labelled market-wide data for every topic the question asks about.

    `topics` come from the router/classifier LLM (chosen by meaning). The keyword detector is only a
    safety net unioned in, so a rewritten query can no longer silently drop a block.
    """
    chosen = [t for t in (topics or []) if t in _BUILDERS]
    topics = chosen + [t for t in detect_market_topics(query, []) if t not in chosen]
    # A ranking asked together with a sector list ("liệt kê ... tăng mạnh") keeps both.
    if not topics:
        topics = ["movers", "index"]
    blocks = [_BUILDERS[t](query) for t in topics if t in _BUILDERS]
    return "\n\n".join(b for b in blocks if b)
