-- Create a read-only Postgres role for the SQL agent.
-- Run once as a superuser (e.g. the main POSTGRES_USER).
-- Idempotent: uses DO block to skip if role already exists.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_readonly') THEN
        CREATE ROLE rag_readonly LOGIN PASSWORD 'readonly_pass';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE ragdb TO rag_readonly;
GRANT USAGE ON SCHEMA public TO rag_readonly;
GRANT SELECT ON financial_facts TO rag_readonly;
GRANT SELECT ON stock_prices TO rag_readonly;
