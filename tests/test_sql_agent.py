"""
tests/test_sql_agent.py — Security attack tests for the SQL agent (Bài 18).

All 10 attacks target validate_sql() — the AST-based security layer.
No LLM calls, no DB connection needed: validate_sql() is pure validation.

Attack categories:
  - forbidden_table:    access to tables outside the allowlist
  - dml_stmt:          INSERT / UPDATE / DELETE
  - ddl_stmt:          DROP / TRUNCATE
  - multiple_stmts:    semicolon-separated statement chains
  - system_table:      access to Postgres system tables (pg_shadow, pg_tables, etc.)
"""
from __future__ import annotations

import pytest

from rag.sql_agent import SecurityError, validate_sql


# ── 10 attack payloads ────────────────────────────────────────────────────────

ATTACKS = [
    # 1. Non-allowed application table
    (
        "forbidden_table",
        "SELECT * FROM users WHERE id = 1",
    ),
    # 2. INSERT — DML
    (
        "insert_dml",
        "INSERT INTO financial_facts (ticker, period, report_type, metric_code, value, unit, source_file, source_page, source) "
        "VALUES ('HACK', '2024', 'standalone', 'roe', 9999, 'VND', 'x', 1, 'pdf')",
    ),
    # 3. UPDATE — DML (mass update)
    (
        "update_dml",
        "UPDATE financial_facts SET value = 0 WHERE 1=1",
    ),
    # 4. DELETE — DML
    (
        "delete_dml",
        "DELETE FROM financial_facts WHERE 1=1",
    ),
    # 5. DROP — DDL
    (
        "drop_ddl",
        "DROP TABLE financial_facts",
    ),
    # 6. TRUNCATE — DDL
    (
        "truncate_ddl",
        "TRUNCATE TABLE financial_facts",
    ),
    # 7. Multiple statements via semicolon
    (
        "multiple_statements",
        "SELECT ticker FROM financial_facts LIMIT 5; DROP TABLE financial_facts",
    ),
    # 8. Comment injection followed by second statement on next line
    (
        "comment_then_delete",
        "SELECT ticker FROM financial_facts -- safe query\n; DELETE FROM financial_facts",
    ),
    # 9. System table (Postgres user passwords)
    (
        "system_table_pg_shadow",
        "SELECT * FROM pg_shadow",
    ),
    # 10. Subquery with forbidden system table
    (
        "subquery_forbidden_table",
        "SELECT sub.tablename FROM (SELECT tablename FROM pg_tables WHERE schemaname = 'public') sub",
    ),
]


@pytest.mark.parametrize("attack_name,sql", ATTACKS)
def test_attack_is_blocked(attack_name: str, sql: str) -> None:
    """Every attack payload must raise SecurityError from validate_sql()."""
    with pytest.raises(SecurityError, match=".+"):
        validate_sql(sql)


# ── safe SQL passes through ───────────────────────────────────────────────────

SAFE_QUERIES = [
    # Basic SELECT
    "SELECT ticker, value FROM financial_facts WHERE period = '2024' LIMIT 10",
    # Aggregation — should get LIMIT injected
    "SELECT ticker, MAX(value) FROM financial_facts WHERE metric_code = 'roe' GROUP BY ticker ORDER BY MAX(value) DESC",
    # Stock prices
    "SELECT trade_date, close_adj FROM stock_prices WHERE ticker = 'HPG' ORDER BY trade_date DESC LIMIT 30",
    # JOIN between allowed tables
    "SELECT f.ticker, f.value, s.close_adj FROM financial_facts f JOIN stock_prices s ON f.ticker = s.ticker WHERE f.period = '2024' LIMIT 20",
]


@pytest.mark.parametrize("sql", SAFE_QUERIES)
def test_safe_query_passes(sql: str) -> None:
    """Legitimate SELECT queries must pass validate_sql() without exception."""
    result = validate_sql(sql)
    assert result.strip().upper().startswith("SELECT")
    # LIMIT must always be present in the output
    assert "LIMIT" in result.upper()


def test_limit_injected_when_missing() -> None:
    sql = "SELECT ticker, value FROM financial_facts WHERE metric_code = 'roe'"
    result = validate_sql(sql)
    assert "LIMIT 1000" in result


def test_existing_limit_preserved() -> None:
    sql = "SELECT * FROM financial_facts LIMIT 5"
    result = validate_sql(sql)
    assert "LIMIT 5" in result
    assert "LIMIT 1000" not in result


# ── integration tests — require live DB + rag_readonly role ───────────────────
# Run with: pytest tests/test_sql_agent.py -m integration -v
# Skip automatically if DB not reachable.

import psycopg2

def _db_available() -> bool:
    try:
        import os
        from core.config import settings
        conn = psycopg2.connect(
            f"host=127.0.0.1 port=5432 dbname={settings.POSTGRES_DB} "
            f"user={os.environ.get('POSTGRES_READONLY_USER', 'rag_readonly')} "
            f"password={os.environ.get('POSTGRES_READONLY_PASSWORD', 'readonly_pass')}"
        )
        conn.close()
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(
    not _db_available(),
    reason="rag_readonly DB not reachable — skipping integration tests",
)


@db_required
@pytest.mark.integration
def test_readonly_role_blocks_delete() -> None:
    """Layer 1: rag_readonly role must reject DELETE even with valid SQL."""
    import os
    from core.config import settings
    conn = psycopg2.connect(
        f"host=127.0.0.1 port=5432 dbname={settings.POSTGRES_DB} "
        f"user={os.environ.get('POSTGRES_READONLY_USER', 'rag_readonly')} "
        f"password={os.environ.get('POSTGRES_READONLY_PASSWORD', 'readonly_pass')}"
    )
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM financial_facts WHERE ticker = '__nonexistent__'")
    finally:
        conn.rollback()
        conn.close()


@db_required
@pytest.mark.integration
def test_execute_safe_financial_facts() -> None:
    """execute_safe() returns rows from financial_facts for a valid question."""
    from rag.sql_agent import execute_safe, QueryResult

    # Use a pre-written SQL question we know will produce rows if data exists
    # We bypass LLM by patching generate_sql inline
    from rag import sql_agent

    original = sql_agent.generate_sql

    def _mock_generate(question, client=None):
        return "SELECT ticker, metric_code, value FROM financial_facts ORDER BY id LIMIT 5"

    sql_agent.generate_sql = _mock_generate
    try:
        result = execute_safe("(mocked)", client=None)
        assert isinstance(result, QueryResult)
        assert "ticker" in result.columns
        assert "value" in result.columns
        # If financial_facts has data, rows should be non-empty
        if result.rows:
            assert all(len(row) == 3 for row in result.rows)
    finally:
        sql_agent.generate_sql = original


@db_required
@pytest.mark.integration
def test_execute_safe_timeout_enforced() -> None:
    """Layer 4: pg_sleep(10) must be killed by statement_timeout = 5s."""
    import time
    from rag.sql_agent import SQLAgentError, execute_safe
    from rag import sql_agent

    original = sql_agent.generate_sql

    def _mock_sleep(question, client=None):
        return "SELECT pg_sleep(10)"

    sql_agent.generate_sql = _mock_sleep
    try:
        t0 = time.perf_counter()
        with pytest.raises((SQLAgentError, Exception)):
            execute_safe("(mocked sleep)", client=None)
        elapsed = time.perf_counter() - t0
        assert elapsed < 8, f"Timeout did not fire in time: {elapsed:.1f}s"
    finally:
        sql_agent.generate_sql = original


@db_required
@pytest.mark.integration
def test_execute_safe_loi_nhuan_cao_nhat() -> None:
    """Lesson checklist: a real aggregation query returns correct data from Postgres."""
    from rag.sql_agent import execute_safe
    from rag import sql_agent

    original = sql_agent.generate_sql

    def _mock_query(question, client=None):
        return (
            "SELECT ticker, period, value "
            "FROM financial_facts "
            "WHERE metric_code = 'loi_nhuan_sau_thue' "
            "ORDER BY value DESC "
            "LIMIT 5"
        )

    sql_agent.generate_sql = _mock_query
    try:
        result = execute_safe("(mocked top loi nhuan)", client=None)
        print("\nTop lợi nhuận sau thuế trong DB:")
        print(result.format_answer())
        assert isinstance(result.rows, list)
        # If data exists, values should be positive numbers
        for row in result.rows:
            assert float(row[2]) > 0, f"Unexpected non-positive value: {row}"
    finally:
        sql_agent.generate_sql = original
