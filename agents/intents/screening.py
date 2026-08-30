"""
agents/intents/screening.py — Nhóm 6: Tổng hợp & Lọc cổ phiếu.

Routes to rag/qa.py SQL path for multi-stock screening queries.
For common ratio-screening patterns (ROE, revenue, profit top-N) we use
pre-built SQL templates to bypass LLM hallucination of non-existent views.
"""

from __future__ import annotations

from datetime import date

from langfuse import observe

from llm.factory import create_client
from llm.types import Message
from agents.intents import strip_preamble, strip_thinking, extract_report

_LATEST_PERIOD = str(date.today().year)

# ── pre-built SQL templates ────────────────────────────────────────────────────
# All operate on financial_facts for period=_LATEST_PERIOD, one row per ticker via DISTINCT ON.

_ROE_TOP_SQL = f"""
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
      AND f1.period = '{_LATEST_PERIOD}'
      AND f2.value > 0
    ORDER BY f1.ticker, roe_pct DESC NULLS LAST
) sub
ORDER BY roe_pct DESC NULLS LAST
LIMIT 20;
"""

_REVENUE_TOP_SQL = f"""
SELECT ticker, revenue_billion_vnd
FROM (
    SELECT DISTINCT ON (ticker)
        ticker,
        ROUND((value / 1e9)::numeric, 0) AS revenue_billion_vnd
    FROM financial_facts
    WHERE metric_code = 'doanh_thu_thuan'
      AND period = '{_LATEST_PERIOD}'
    ORDER BY ticker, value DESC NULLS LAST
) sub
ORDER BY revenue_billion_vnd DESC NULLS LAST
LIMIT 20;
"""

_PROFIT_TOP_SQL = f"""
SELECT ticker, profit_billion_vnd
FROM (
    SELECT DISTINCT ON (ticker)
        ticker,
        ROUND((value / 1e9)::numeric, 0) AS profit_billion_vnd
    FROM financial_facts
    WHERE metric_code IN ('lailo_thuan_sau_thue', 'loi_nhuan_sau_thue')
      AND period = '{_LATEST_PERIOD}'
    ORDER BY ticker, value DESC NULLS LAST
) sub
ORDER BY profit_billion_vnd DESC NULLS LAST
LIMIT 20;
"""

# "đáng chú ý / nổi bật": top stocks by ROE with positive profit
_NOTABLE_SQL = f"""
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
      AND f1.period = '{_LATEST_PERIOD}'
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


def _pick_template(query: str) -> str | None:
    """Return pre-built SQL if query matches a known screening pattern."""
    lower = query.lower()
    if "roe" in lower:
        return _ROE_TOP_SQL
    if "doanh thu" in lower and ("cao nhất" in lower or "top" in lower or "lớn nhất" in lower):
        return _REVENUE_TOP_SQL
    if "lợi nhuận" in lower and ("cao nhất" in lower or "top" in lower or "lớn nhất" in lower):
        return _PROFIT_TOP_SQL
    if any(pat in lower for pat in _NOTABLE_PATTERNS):
        return _NOTABLE_SQL
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
            "Bọc toàn bộ câu trả lời trong <report> và </report>. "
            "KHÔNG nhắc tên cột DB. Trả lời TRỰC TIẾP."
        ),
        max_tokens=512,
        temperature=0,
    )
    return strip_thinking(strip_preamble(extract_report(resp.text.strip())))


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
