-- The per-user transaction ledger moved to the separate vn-portfolio app (its own "portfolio" database).
-- The table was empty when dropped (verified 2026-10-21); see docs/plans/portfolio-api.md.
DROP TABLE IF EXISTS portfolio_transactions;
