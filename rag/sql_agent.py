"""
rag/sql_agent.py — Safe SQL generation and execution for the hybrid RAG pipeline (Bài 18).

Security architecture (4 layers, outermost first):
  1. Read-only Postgres role  — rag_readonly cannot INSERT/UPDATE/DELETE/DROP even if
                                 all other layers fail.
  2. sqlglot AST validation   — parse SQL before execution; block forbidden statement
                                 types and table names. AST-based, not regex — immune
                                 to comment injection, unicode lookalikes, whitespace tricks.
  3. LIMIT guard              — inject LIMIT 1000 on any SELECT that omits it, preventing
                                 accidental full-table dumps.
  4. statement_timeout        — SET statement_timeout = '5s' at session level; kills runaway
                                 queries (pg_sleep, cross-joins, etc.) before they finish.

Why not regex? Regex over raw SQL text can be defeated by:
  - SQL comments:  SELECT/*DROP*/ticker FROM financial_facts
  - Unicode lookalikes: ＤＲＯＰ TABLE
  - Inline newlines inside keywords
  sqlglot parses the token stream, so all these tricks are invisible to it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date

import psycopg2
import psycopg2.extras
import sqlglot
import sqlglot.expressions as exp

from core.config import settings
from llm.types import Message

# ── allowed tables ────────────────────────────────────────────────────────────

ALLOWED_TABLES: frozenset[str] = frozenset({"financial_facts", "stock_prices", "securities"})

# Statement type names that are always forbidden (covers all dialects).
# Using name strings for robustness across sqlglot versions.
_FORBIDDEN_STMT_NAMES: frozenset[str] = frozenset({
    "Insert", "Update", "Delete",
    "Drop", "Create", "Alter",
    "Command",        # VACUUM, TRUNCATE in some sqlglot versions
    "TruncateTable",  # sqlglot >= 17
})

# ── schema context for LLM ────────────────────────────────────────────────────

_LATEST_YEAR = date.today().year

_SCHEMA_CONTEXT = f"""\
PostgreSQL database schema (READ-ONLY):

TABLE financial_facts
  ticker       TEXT     -- stock ticker, e.g. 'HPG'
  period       TEXT     -- e.g. '{_LATEST_YEAR}', '{_LATEST_YEAR - 1}', 'Q3/{_LATEST_YEAR}'
  report_type  TEXT     -- 'standalone' or 'consolidated'
  metric_code  TEXT     -- actual codes in DB (use ILIKE or exact match):
                        --   Revenue:   'doanh_thu_thuan', 'doanh_thu_ban_hang_va_cung_cap_dich_vu'
                        --   Profit:    'lailo_thuan_sau_thue', 'loi_nhuan_truoc_thue', 'loi_nhuan_gop'
                        --   Assets:    'tong_tai_san', 'tai_san_ngan_han', 'tai_san_dai_han'
                        --   Equity:    'von_chu_so_huu', 'von_gop'
                        --   Debt:      'no_phai_tra', 'vay_ngan_han', 'vay_dai_han'
                        --   Cash flow: 'luu_chuyen_tien_te_rong_tu_cac_hoat_ong_san_xuat_kinh_doanh'
                        -- When user asks broadly (e.g. "doanh thu lợi nhuận"), do NOT filter by metric_code.
                        -- Use: SELECT metric_code, value FROM financial_facts WHERE ticker=... AND period=...
  value        NUMERIC  -- raw value in VND (billions: divide by 1e9 for display)
  unit         TEXT     -- 'VND'
  source       TEXT     -- 'pdf' or 'vnstock'

TABLE stock_prices
  ticker       TEXT
  trade_date   DATE
  close_adj    NUMERIC  -- adjusted close price
  volume       BIGINT

TABLE securities
  ticker       TEXT PRIMARY KEY
  exchange     TEXT     -- 'HOSE', 'HNX', 'UPCOM'
  sector       TEXT     -- e.g. 'Thép', 'Ngân hàng', 'Công nghệ'
  industry     TEXT
  company_name TEXT

Period filter rules:
- Annual data: WHERE period = '{_LATEST_YEAR}'
- Quarterly: WHERE period LIKE 'Q%/{_LATEST_YEAR}' (e.g. 'Q3/{_LATEST_YEAR}')
- Any {_LATEST_YEAR} data: WHERE period = '{_LATEST_YEAR}' OR period LIKE '%/{_LATEST_YEAR}'

Example — top 5 ROE cao nhất (một dòng mỗi ticker, period='{_LATEST_YEAR}'):
SELECT DISTINCT ON (f1.ticker)
       f1.ticker,
       ROUND((f1.value / NULLIF(f2.value, 0) * 100)::numeric, 2) AS roe_pct
FROM financial_facts f1
JOIN financial_facts f2
  ON f1.ticker = f2.ticker
  AND f1.period = f2.period
  AND f1.report_type = f2.report_type
WHERE f1.metric_code = 'lailo_thuan_sau_thue'
  AND f2.metric_code = 'von_chu_so_huu'
  AND f1.period = '{_LATEST_YEAR}'
ORDER BY f1.ticker, roe_pct DESC NULLS LAST
LIMIT 5;
-- IMPORTANT: DISTINCT ON prevents duplicate rows per ticker.
-- roe_pct should be a % (e.g. 15.50 means 15.5%). Values in DB are raw VND.
-- ROUND requires ::numeric cast in PostgreSQL.

Rules:
- Generate PostgreSQL SELECT queries ONLY.
- Return ONLY the SQL query — no explanation, no markdown fences, no notes after the query.
- End the query with a semicolon (;).
- Do NOT include any DML (INSERT/UPDATE/DELETE) or DDL (CREATE/DROP/ALTER).
- Use ONLY the three tables: financial_facts, stock_prices, securities. NO other tables or views.
- NEVER use: latest, latest_annual, company_metrics, financials, or any unlisted name.
- When using ROUND with a decimal places argument, always cast to ::numeric first: ROUND(expr::numeric, 2).
- Keep queries focused on the user's question."""

_SQL_SYSTEM = (
    "You are a PostgreSQL query generator for a financial database.\n\n"
    + _SCHEMA_CONTEXT
)

# ── exceptions ────────────────────────────────────────────────────────────────


class SecurityError(Exception):
    """Raised when SQL fails a security check before execution."""


class SQLAgentError(Exception):
    """Raised when SQL generation or execution fails."""


# ── result type ───────────────────────────────────────────────────────────────


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[dict] = field(default_factory=list)

    def format_answer(self) -> str:
        if not self.rows:
            return "Không có dữ liệu thỏa mãn điều kiện."
        header = " | ".join(self.columns)
        sep = "-" * max(len(header), 40)
        data = "\n".join(" | ".join(str(row[c]) for c in self.columns) for row in self.rows)
        return f"{header}\n{sep}\n{data}"

    def as_context(self) -> str:
        return f"[SQL]\n{self.sql}\n\n[KẾT QUẢ]\n{self.format_answer()}"


# ── security validation ───────────────────────────────────────────────────────


def validate_sql(sql: str) -> str:
    """
    Validate and harden SQL through layers 2 & 3.

    Returns the (possibly LIMIT-injected) safe SQL string.
    Raises SecurityError on any violation.

    Layer 1 (read-only role) and Layer 4 (timeout) are enforced at execution time.
    """
    sql = sql.strip()
    if not sql:
        raise SecurityError("Empty SQL.")

    # --- Parse (catches unicode lookalikes, invalid SQL) ---
    try:
        stmts = sqlglot.parse(sql, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:
        raise SecurityError(f"SQL parse failed: {exc}") from exc

    if not stmts:
        raise SecurityError("SQL produced no parse tree.")

    # --- Block multiple statements ---
    if len(stmts) > 1:
        raise SecurityError(
            f"Multiple statements detected ({len(stmts)}). Only single SELECT allowed."
        )

    stmt = stmts[0]

    # --- Block forbidden statement types ---
    stmt_type = type(stmt).__name__
    if stmt_type in _FORBIDDEN_STMT_NAMES:
        raise SecurityError(f"Forbidden statement type: {stmt_type}.")

    if not isinstance(stmt, exp.Select):
        raise SecurityError(f"Only SELECT is allowed; got: {stmt_type}.")

    # --- Block forbidden tables (walk entire AST, catches subqueries) ---
    for table_node in stmt.find_all(exp.Table):
        name = table_node.name.lower()
        if name and name not in ALLOWED_TABLES:
            raise SecurityError(
                f"Access to table '{name}' is not allowed. "
                f"Allowed: {sorted(ALLOWED_TABLES)}."
            )

    # --- Inject LIMIT 1000 if missing (layer 3) ---
    if stmt.find(exp.Limit) is None:
        stmt = stmt.limit(1000)

    return stmt.sql(dialect="postgres")


# ── SQL generation ────────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """Remove markdown SQL code fences if present."""
    text = re.sub(r"```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    return text.strip().rstrip("`").strip()


def _extract_sql(text: str) -> str:
    """Extract the last SELECT statement from LLM output.

    Handles DeepSeek reasoning prefix and trailing commentary.
    Priority order for termination: semicolon > blank line > end of text.
    """
    text = _strip_fences(text)
    # Find the last SELECT keyword (case-insensitive)
    idx = text.upper().rfind("SELECT")
    if idx == -1:
        return text.strip()
    sql = text[idx:].strip()

    # 1. Cut at semicolon (clean SQL terminator)
    semi = sql.find(";")
    if semi != -1:
        return sql[: semi + 1].strip()

    # 2. Cut at blank line (trailing LLM commentary)
    if "\n\n" in sql:
        first_part = sql.split("\n\n")[0].strip()
        if "SELECT" in first_part.upper():
            return first_part

    return sql


def generate_sql(question: str, client=None) -> str:
    """Ask LLM to generate a SQL query for the given question.

    Returns raw SQL string (not yet validated).
    Raises SQLAgentError if LLM fails or returns no content.
    """
    if client is None:
        from llm.factory import create_client
        client = create_client()

    resp = client.generate(
        messages=[Message(role="user", content=question)],
        system=_SQL_SYSTEM,
        max_tokens=2048,
    )
    sql = _extract_sql(resp.text)
    if not sql:
        raise SQLAgentError("LLM returned empty SQL.")
    return sql


# ── readonly connection ───────────────────────────────────────────────────────


def _readonly_dsn() -> str:
    user = os.environ.get("POSTGRES_READONLY_USER", "rag_readonly")
    password = os.environ.get("POSTGRES_READONLY_PASSWORD", "readonly_pass")
    return (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} "
        f"user={user} "
        f"password={password}"
    )


# ── full pipeline ─────────────────────────────────────────────────────────────


def run_raw_sql(sql: str) -> QueryResult:
    """Validate + execute a pre-built SQL string (no LLM generation step)."""
    safe_sql = validate_sql(sql)
    try:
        conn = psycopg2.connect(_readonly_dsn())
    except psycopg2.OperationalError as exc:
        raise SQLAgentError(f"DB connection failed: {exc}") from exc
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET statement_timeout = '5s'")
            cur.execute(safe_sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return QueryResult(sql=safe_sql, columns=columns, rows=rows)
    except psycopg2.Error as exc:
        conn.rollback()
        raise SQLAgentError(f"Query execution failed: {exc}") from exc
    finally:
        conn.close()


def execute_safe(question: str, client=None) -> QueryResult:
    """
    Full pipeline: generate SQL → validate (layers 2+3) → execute (layers 1+4).

    Raises SecurityError if validation fails.
    Raises SQLAgentError if generation or execution fails.
    """
    raw_sql = generate_sql(question, client=client)

    try:
        safe_sql = validate_sql(raw_sql)
    except SecurityError:
        raise  # propagate without wrapping

    try:
        conn = psycopg2.connect(_readonly_dsn())
    except psycopg2.OperationalError as exc:
        raise SQLAgentError(f"DB connection failed: {exc}") from exc

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Layer 4: statement timeout
            cur.execute("SET statement_timeout = '5s'")
            cur.execute(safe_sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return QueryResult(sql=safe_sql, columns=columns, rows=rows)
    except psycopg2.Error as exc:
        conn.rollback()
        raise SQLAgentError(f"Query execution failed: {exc}") from exc
    finally:
        conn.close()
