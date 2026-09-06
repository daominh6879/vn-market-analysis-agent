"""
agents/intents/screening.py — Nhóm 6: Tổng hợp & Lọc cổ phiếu.

Routes to rag/qa.py SQL path for multi-stock screening queries.
For common ratio-screening patterns (ROE, revenue, profit top-N) we use
pre-built SQL templates to bypass LLM hallucination of non-existent views.
"""

from __future__ import annotations

import math
import re

import pandas as pd
from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking, extract_report, NO_THINKING_INSTR

# ── Latest available period ───────────────────────────────────────────────────

_period_cache: str | None = None


def _get_latest_period(as_of_date: str | None = None) -> str:
    """Query DB for the most recent 4-digit year period in financial_facts.

    as_of_date (ISO YYYY-MM-DD): cap the period at that date's year — e.g. "năm ngoái"
    → latest year <= last year. None → absolute latest (cached).
    """
    global _period_cache
    if as_of_date is None and _period_cache:
        return _period_cache
    year = as_of_date[:4] if as_of_date else None
    try:
        from core.db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                if year:
                    cur.execute(
                        "SELECT MAX(period) FROM financial_facts "
                        "WHERE period ~ '^[0-9]{4}$' AND period <= %s",
                        (year,),
                    )
                else:
                    cur.execute(
                        "SELECT MAX(period) FROM financial_facts "
                        "WHERE period ~ '^[0-9]{4}$'"
                    )
                row = cur.fetchone()
                if row and row[0]:
                    period = str(row[0])
                    if as_of_date is None:
                        _period_cache = period
                    return period
    except Exception:
        pass
    from datetime import date
    if as_of_date:
        fallback = str(date.fromisoformat(as_of_date).year - 2)
    else:
        fallback = str(date.today().year - 2)
    if as_of_date is None:
        _period_cache = fallback
    return fallback


# ── pre-built SQL templates ────────────────────────────────────────────────────
# All operate on financial_facts for the latest available period.
# Use _get_latest_period() at call time — NOT a module-level f-string constant.

def _roe_top_sql(period: str) -> str:
    return f"""
SELECT ticker, roe_pct
FROM (
    SELECT DISTINCT ON (f1.ticker)
        f1.ticker,
        ROUND((f1.value / NULLIF(f2.value, 0) * 100)::numeric, 2) AS roe_pct
    FROM financial_facts f1
    JOIN financial_facts f2
        ON f1.ticker = f2.ticker
        AND f1.period = f2.period
        AND f1.report_type = f2.report_type
    WHERE f1.metric_code IN ('lailo_thuan_sau_thue', 'loi_nhuan_sau_thue')
      AND f2.metric_code = 'von_chu_so_huu'
      AND f1.period = '{period}'
      AND f2.value > 0
    ORDER BY f1.ticker, roe_pct DESC NULLS LAST
) sub
ORDER BY roe_pct DESC NULLS LAST
LIMIT 20;
"""


def _revenue_top_sql(period: str) -> str:
    return f"""
SELECT ticker, revenue_billion_vnd
FROM (
    SELECT DISTINCT ON (ticker)
        ticker,
        ROUND((value / 1e9)::numeric, 0) AS revenue_billion_vnd
    FROM financial_facts
    WHERE metric_code = 'doanh_thu_thuan'
      AND period = '{period}'
    ORDER BY ticker, value DESC NULLS LAST
) sub
ORDER BY revenue_billion_vnd DESC NULLS LAST
LIMIT 20;
"""


def _profit_top_sql(period: str) -> str:
    return f"""
SELECT ticker, profit_billion_vnd
FROM (
    SELECT DISTINCT ON (ticker)
        ticker,
        ROUND((value / 1e9)::numeric, 0) AS profit_billion_vnd
    FROM financial_facts
    WHERE metric_code IN ('lailo_thuan_sau_thue', 'loi_nhuan_sau_thue')
      AND period = '{period}'
    ORDER BY ticker, value DESC NULLS LAST
) sub
ORDER BY profit_billion_vnd DESC NULLS LAST
LIMIT 20;
"""


def _notable_sql(period: str) -> str:
    return f"""
SELECT ticker, roe_pct, profit_bil
FROM (
    SELECT DISTINCT ON (f1.ticker)
        f1.ticker,
        ROUND((f1.value / NULLIF(f2.value, 0) * 100)::numeric, 2) AS roe_pct,
        ROUND((f1.value / 1e9)::numeric, 0) AS profit_bil
    FROM financial_facts f1
    JOIN financial_facts f2
        ON f1.ticker = f2.ticker
        AND f1.period = f2.period
        AND f1.report_type = f2.report_type
    WHERE f1.metric_code IN ('lailo_thuan_sau_thue', 'loi_nhuan_sau_thue')
      AND f2.metric_code = 'von_chu_so_huu'
      AND f1.period = '{period}'
      AND f2.value > 0
      AND f1.value > 0
    ORDER BY f1.ticker, roe_pct DESC NULLS LAST
) sub
ORDER BY roe_pct DESC NULLS LAST
LIMIT 15;
"""

_NOTABLE_PATTERNS = frozenset({
    "đáng chú ý", "nổi bật", "đáng quan tâm", "đáng mua",
    "tiềm năng", "tốt nhất", "tích lũy", "đáng đầu tư",
    "cần chú ý", "đáng theo dõi", "khuyến nghị",
})


def _pick_template(query: str, as_of_date: str | None = None) -> str | None:
    """Return pre-built SQL if query matches a known screening pattern."""
    lower = query.lower()
    period = _get_latest_period(as_of_date)
    if "roe" in lower:
        return _roe_top_sql(period)
    if "doanh thu" in lower and ("cao nhất" in lower or "top" in lower or "lớn nhất" in lower):
        return _revenue_top_sql(period)
    if "lợi nhuận" in lower and ("cao nhất" in lower or "top" in lower or "lớn nhất" in lower):
        return _profit_top_sql(period)
    if any(pat in lower for pat in _NOTABLE_PATTERNS):
        return _notable_sql(period)
    return None


def _narrate(query: str, rows_text: str) -> str:
    client = create_client()
    resp = client.generate(
        [Message(
            role="user",
            content=(
                f"Câu hỏi: {query}\n\n"
                f"Kết quả từ DB:\n{rows_text}\n\n"
                "Trả lời ngắn gọn bằng tiếng Việt, liệt kê kết quả rõ ràng."
            ),
        )],
        system=(
            "Bạn là trợ lý phân tích tài chính. Tóm tắt kết quả lọc cổ phiếu từ dữ liệu đã cho. "
            "KHÔNG nhắc tên cột DB. Trả lời TRỰC TIẾP. "
            + NO_THINKING_INSTR
        ),
        max_tokens=512,
        temperature=0,
    )
    return strip_thinking(strip_preamble(extract_report(resp.text.strip())))


# ── Screening filters ─────────────────────────────────────────────────────────
# A screening query ("lọc P/E < 10", "RSI < 30", "ROE > 20 AND RSI < 30") is a LIST of
# numeric filters {indicator, op, threshold} extracted by the router/classifier LLM. Two
# sources back them:
#   - fundamental ratios → stock_ratios (one snapshot row per ticker)
#   - technical indicators → ohlcv_daily (computed per ticker)
# Filters within one source AND-ed in a single pass; across sources the surviving ticker
# sets are intersected.

# Fundamental indicator → stock_ratios column.
_FUNDAMENTAL_COLS: dict[str, str] = {
    "pe": "pe", "p/e": "pe",
    "pb": "pb", "p/b": "pb",
    "roe": "roe_pct",
    "roa": "roa_pct",
    "eps": "eps",
    "ev_ebitda": "ev_ebitda", "ev/ebitda": "ev_ebitda",
    "de": "de_ratio", "d/e": "de_ratio",
    "gross_margin": "gross_margin_pct",
    "net_margin": "net_margin_pct",
    "revenue_growth": "revenue_growth_pct",
    "earnings_growth": "earnings_growth_pct",
}

# Absolute fundamental figures → financial_facts metric_code(s). Values are returned in
# tỷ VND (value / 1e9) for the latest annual period.
_FACTS_METRICS: dict[str, tuple[str, ...]] = {
    "revenue": ("doanh_thu_thuan",),
    "profit": ("lai_lo_thuan_sau_thue", "loi_nhuan_sau_thue"),
}

_RSI_FILTER_RE = re.compile(
    r"rsi\s*(?P<op>dưới|trên|nhỏ hơn|lớn hơn|thấp hơn|cao hơn|<=|>=|<|>|=)?\s*(?P<n>\d{1,3})",
    re.IGNORECASE,
)

_ABOVE_OPS = frozenset({"trên", "lớn hơn", "cao hơn", ">", ">="})


def _parse_rsi_filter(query: str) -> tuple[str, int] | None:
    """Return (op, threshold) for an RSI filter query, or None if not an RSI screen.

    Regex fallback only — the router/classifier LLM is the primary extractor.
    """
    m = _RSI_FILTER_RE.search(query or "")
    if not m:
        return None
    op = ">" if (m.group("op") or "") in _ABOVE_OPS else "<"
    return op, int(m.group("n"))


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _cmp(v: float, op: str, threshold: float) -> bool:
    if op == "<":
        return v < threshold
    if op == "<=":
        return v <= threshold
    if op == ">":
        return v > threshold
    return v >= threshold


def _matches_all(values: dict, filters: list[dict]) -> bool:
    """All filters must pass on `values` (indicator → numeric). Missing/NaN fails."""
    for f in filters:
        v = _to_float(values.get(f["indicator"]))
        if v is None or not _cmp(v, f["op"], f["threshold"]):
            return False
    return True


def _normalize_filters(screening) -> list[dict]:
    """Accept None | single dict | list of dicts → list."""
    if not screening:
        return []
    if isinstance(screening, dict):
        return [screening]
    return [f for f in screening if isinstance(f, dict)]


# ── Technical indicator computation (ohlcv_daily) ─────────────────────────────

def _last_rsi(df: pd.DataFrame) -> float | None:
    try:
        import pandas_ta  # noqa: F401  (registers df.ta accessor)
        return _to_float(df.ta.rsi(length=14).iloc[-1])
    except Exception:
        return None


def _last_macd_hist(df: pd.DataFrame) -> float | None:
    try:
        import pandas_ta  # noqa: F401
        macd = df.ta.macd(fast=12, slow=26, signal=9)
        return _to_float(macd["MACDh_12_26_9"].iloc[-1])
    except Exception:
        return None


def _volume_ratio(df: pd.DataFrame) -> float | None:
    if "volume" not in df.columns or len(df) < 21:
        return None
    try:
        last = float(df["volume"].iloc[-1])
        avg = float(df["volume"].iloc[-21:-1].mean())
        return last / avg if avg > 0 else None
    except Exception:
        return None


def _last_adx(df: pd.DataFrame) -> float | None:
    try:
        import pandas_ta  # noqa: F401
        return _to_float(df.ta.adx(length=14)["ADX_14"].iloc[-1])
    except Exception:
        return None


def _pct_vs_ma(df: pd.DataFrame, n: int) -> float | None:
    """(close − SMA(n)) / SMA(n) × 100 — positive = price above the MA."""
    if "close" not in df.columns or len(df) < n:
        return None
    try:
        import pandas_ta  # noqa: F401
        ma = _to_float(df.ta.sma(length=n).iloc[-1])
        close = _to_float(df["close"].iloc[-1])
        if ma is None or close is None or ma == 0:
            return None
        return (close - ma) / ma * 100
    except Exception:
        return None


def _pct_from_52w(df: pd.DataFrame, col: str) -> float | None:
    """(close − 52w high/low) / 52w high/low × 100."""
    if col not in df.columns or "close" not in df.columns:
        return None
    try:
        extreme = float(df[col].max() if col == "high" else df[col].min())
        close = float(df["close"].iloc[-1])
        if extreme == 0:
            return None
        return (close - extreme) / extreme * 100
    except Exception:
        return None


_TECHNICAL_FNS: dict[str, object] = {
    "rsi": _last_rsi,
    "macd": _last_macd_hist,
    "volume": _volume_ratio,
    "adx": _last_adx,
    "ma20": lambda df: _pct_vs_ma(df, 20),
    "ma50": lambda df: _pct_vs_ma(df, 50),
    "ma200": lambda df: _pct_vs_ma(df, 200),
    "pct_52w_high": lambda df: _pct_from_52w(df, "high"),
    "pct_52w_low": lambda df: _pct_from_52w(df, "low"),
}


# ── Screeners ────────────────────────────────────────────────────────────────

def _screen_fundamental(tickers: list[str], filters: list[dict]) -> list[tuple[str, dict]]:
    from core.db import get_conn
    cols: list[str] = []
    for f in filters:
        c = _FUNDAMENTAL_COLS.get(f["indicator"])
        if c and c not in cols:
            cols.append(c)
    if not cols:
        return []
    sql = f"SELECT ticker, {', '.join(cols)} FROM stock_ratios WHERE ticker = ANY(%s)"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (list(tickers),))
                rows = cur.fetchall()
    except Exception:
        return []
    out: list[tuple[str, dict]] = []
    for row in rows:
        ticker = row[0]
        col_vals = {c: _to_float(row[1 + i]) for i, c in enumerate(cols)}
        values = {f["indicator"]: col_vals.get(_FUNDAMENTAL_COLS.get(f["indicator"])) for f in filters}
        if _matches_all(values, filters):
            out.append((ticker, values))
    return out


def _screen_technical(tickers: list[str], filters: list[dict], end_date: str | None) -> list[tuple[str, dict]]:
    from tools.price import get_historical_ohlcv
    out: list[tuple[str, dict]] = []
    for t in tickers[:20]:
        r = get_historical_ohlcv(t, days=300, end_date=end_date)
        if r.status != "ok" or r.data is None:
            continue
        values = {f["indicator"]: _TECHNICAL_FNS[f["indicator"]](r.data) for f in filters}
        if _matches_all(values, filters):
            out.append((t, values))
    return out


def _screen_financial_facts(tickers: list[str], filters: list[dict], as_of_date: str | None) -> list[tuple[str, dict]]:
    """Absolute revenue/profit threshold via financial_facts (latest annual period, tỷ VND)."""
    from core.db import get_conn
    period = _get_latest_period(as_of_date)
    per_ticker: dict[str, dict] = {}
    for f in filters:
        codes = _FACTS_METRICS.get(f["indicator"])
        if not codes:
            continue
        sql = (
            "SELECT ticker, ROUND((MAX(value)/1e9)::numeric, 1) "
            "FROM financial_facts "
            "WHERE metric_code = ANY(%s) AND period = %s AND ticker = ANY(%s) "
            "GROUP BY ticker"
        )
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (list(codes), period, list(tickers)))
                    for ticker, val in cur.fetchall():
                        per_ticker.setdefault(ticker, {})[f["indicator"]] = _to_float(val)
        except Exception:
            pass
    out: list[tuple[str, dict]] = []
    for t, vals in per_ticker.items():
        if _matches_all(vals, filters):
            out.append((t, vals))
    return out


def gather_data(ticker: str | None, query: str, time_context: dict | None = None,
                screening=None) -> str:
    """Execute screening and return raw rows — no LLM narration.

    screening: structured filters [{indicator, op, threshold}, ...] from the router/
    classifier LLM (or a single dict). When absent, regex fallback (RSI only) then the
    legacy top-N SQL templates.
    """
    from agents.focus import extract_focus_sector
    from core.tickers import get_sector_tickers, get_tickers

    tc = time_context or {}
    end = tc.get("end_date") if tc.get("explicit") else None

    filters = _normalize_filters(screening)
    if not filters:
        parsed = _parse_rsi_filter(query)
        if parsed:
            filters = [{"indicator": "rsi", "op": parsed[0], "threshold": parsed[1]}]

    if filters:
        label = extract_focus_sector(query)
        tickers = get_sector_tickers(label) if label else get_tickers()
        scope = f"ngành {label}" if label else "toàn thị trường"
        if not tickers:
            return f"[SCREENING]\nKhông xác định được danh sách mã cho {scope}."

        fund_filters = [f for f in filters if f["indicator"] in _FUNDAMENTAL_COLS]
        facts_filters = [f for f in filters if f["indicator"] in _FACTS_METRICS]
        tech_filters = [f for f in filters if f["indicator"] in _TECHNICAL_FNS]
        unknown = [f for f in filters
                   if f["indicator"] not in _FUNDAMENTAL_COLS
                   and f["indicator"] not in _FACTS_METRICS
                   and f["indicator"] not in _TECHNICAL_FNS]

        fund_hits = _screen_fundamental(tickers, fund_filters) if fund_filters else None
        facts_hits = _screen_financial_facts(tickers, facts_filters, end) if facts_filters else None
        tech_hits = _screen_technical(tickers, tech_filters, end) if tech_filters else None

        # Intersect surviving tickers across sources (each source AND-ed internally).
        hit_sets = [h for h in (fund_hits, facts_hits, tech_hits) if h is not None]
        if not hit_sets:
            hits = []
        else:
            common = {t for t, _ in hit_sets[0]}
            for h in hit_sets[1:]:
                common &= {t for t, _ in h}
            merged: dict[str, dict] = {}
            for h in hit_sets:
                for t, vals in h:
                    if t in common:
                        merged.setdefault(t, {}).update(vals)
            hits = sorted(((t, merged[t]) for t in common if t in merged), key=lambda x: x[0])

        cond = " AND ".join(f"{f['indicator']} {f['op']} {f['threshold']}" for f in filters)
        if unknown:
            cond += f" [bỏ qua chỉ tiêu không hỗ trợ: {', '.join(f['indicator'] for f in unknown)}]"
        if not hits:
            return f"[SCREENING]\nKhông có mã nào trong {scope} thỏa {cond}."

        lines = []
        for t, vals in hits[:20]:
            detail = ", ".join(f"{k}={v:.2f}" for k, v in vals.items() if v is not None)
            lines.append(f"- {t}: {detail}")
        return f"[SCREENING]\nLọc {scope} ({cond}):\n" + "\n".join(lines)

    # No structured/regex filter → legacy top-N SQL templates.
    sql = _pick_template(query, as_of_date=end)
    if sql:
        try:
            from rag.sql_agent import run_raw_sql
            result = run_raw_sql(sql)
            if result.rows:
                return f"[SCREENING]\n{result.format_answer()}"
            return "[SCREENING]\nKhông có dữ liệu trong database cho tiêu chí này."
        except Exception as exc:
            return f"[SCREENING]\nLỗi SQL: {exc}"
    return "[SCREENING]\nKhông có template SQL phù hợp cho câu hỏi này."


@observe(name="intent.screening")
def run(ticker: str | None, query: str) -> str:
    """Execute screening — pre-built SQL template first, fall back to LLM-generated SQL."""
    sql = _pick_template(query)
    if sql:
        try:
            from rag.sql_agent import run_raw_sql, SQLAgentError
            result = run_raw_sql(sql)
            if result.rows:
                rows_text = result.format_answer()
                return _narrate(query, rows_text)
            return "Không có dữ liệu trong database cho tiêu chí này."
        except Exception:
            pass  # fall through to LLM path

    # LLM-generated SQL path (for queries without a matching template)
    from rag.qa import answer as qa_answer
    return qa_answer(query, ticker=ticker)
