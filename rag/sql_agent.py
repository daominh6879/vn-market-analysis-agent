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

_SCHEMA_CONTEXT = """\
PostgreSQL database schema (READ-ONLY):

TABLE financial_facts
  ticker       TEXT     -- stock ticker, e.g. 'HPG'
  period       TEXT     -- e.g. '2024', '2023', 'Q3/2024'
  report_type  TEXT     -- 'standalone' or 'consolidated'
  metric_code  TEXT     -- e.g. 'tong_tai_san', 'doanh_thu_thuan', 'loi_nhuan_sau_thue',
                        --      'von_chu_so_huu', 'no_phai_tra', 'roe', 'eps'
  value        NUMERIC  -- raw value in VND (or ratio for roe/eps)
  unit         TEXT     -- 'VND' or 'ratio'
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
- Annual data: WHERE period = '2024'
- Quarterly: WHERE period LIKE 'Q%/2024' (e.g. 'Q3/2024')
- Any 2024 data: WHERE period = '2024' OR period LIKE '%/2024'

Rules:
- Generate PostgreSQL SELECT queries ONLY.
- Return ONLY the SQL query, no explanation, no markdown fences.
- Do NOT include any DML (INSERT/UPDATE/DELETE) or DDL (CREATE/DROP/ALTER).
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
        data = "\n".join(" | ".join(str(v) for v in row) for row in self.rows)
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
    """Extract the last SELECT statement from text.

    Handles DeepSeek reasoning prefix: the model may output chain-of-thought
    before the final SQL. We find the last occurrence of SELECT and take
    from there to the end of the statement.
    """
    text = _strip_fences(text)
    # Find the last SELECT keyword (case-insensitive)
    idx = text.upper().rfind("SELECT")
    if idx == -1:
        return text.strip()
    sql = text[idx:].strip()
    # Trim trailing reasoning: only split on blank line if the part after it
    # does NOT look like SQL continuation (avoid truncating CTEs).
    if "\n\n" in sql:
        first_part = sql.split("\n\n")[0].strip()
        upper_first = first_part.upper()
        # If the first part has no SELECT it's probably a CTE fragment — keep all
        if "SELECT" in upper_first:
            sql = first_part
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
        max_tokens=1024,
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
        f"host=127.0.0.1 port=5432 "
        f"dbname={settings.POSTGRES_DB} "
        f"user={user} "
        f"password={password}"
    )


# ── full pipeline ─────────────────────────────────────────────────────────────


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
