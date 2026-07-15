-- ============================================================================
-- Ledger Immutability Protection: Append-Only, Tamper-Evident Ledger
-- ============================================================================

-- Block UPDATE operations on ledger_entries
-- Rationale: A tamper-evident ledger must be append-only. Allowing updates
-- defeats the hash chain and makes verification meaningless.
CREATE OR REPLACE FUNCTION block_ledger_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: UPDATE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_ledger_update ON ledger_entries;
CREATE TRIGGER prevent_ledger_update
BEFORE UPDATE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION block_ledger_update();


-- Block DELETE operations on ledger_entries
-- Rationale: A tamper-evident ledger must be immutable. Deletes erase audit
-- history and break the hash chain. All decisions (approvals and rejections)
-- must remain in the record forever.
CREATE OR REPLACE FUNCTION block_ledger_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is immutable: DELETE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_ledger_delete ON ledger_entries;
CREATE TRIGGER prevent_ledger_delete
BEFORE DELETE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION block_ledger_delete();


-- Block TRUNCATE on ledger_entries
-- Rationale: row-level BEFORE DELETE/UPDATE triggers above do NOT fire on
-- TRUNCATE (Postgres fires TRUNCATE triggers separately, statement-level
-- only). Without this, TRUNCATE ledger_entries; empties the entire
-- tamper-evident audit trail in one statement, bypassing both triggers
-- above entirely -- confirmed live: it succeeded even with both row
-- triggers installed and even as a non-owner role, because TRUNCATE only
-- requires TRUNCATE privilege on the table, not row-trigger permission.
CREATE OR REPLACE FUNCTION block_ledger_truncate()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: TRUNCATE not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_ledger_truncate ON ledger_entries;
CREATE TRIGGER prevent_ledger_truncate
BEFORE TRUNCATE ON ledger_entries
FOR EACH STATEMENT
EXECUTE FUNCTION block_ledger_truncate();


-- ============================================================================
-- Optional: Create a non-superuser role for the application
-- ============================================================================
-- This provides defense-in-depth: even if someone tries to run UPDATE/DELETE
-- via the app connection, the role lacks the permission AND the triggers
-- will also block it.
--
-- To use this, update the app to connect as 'ledger_reader' instead of 'iceberg'.
-- Then the app cannot UPDATE/DELETE even if a developer tries to, and cannot
-- escape to superuser operations.

-- Create the read-only ledger role (if it doesn't exist)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_reader') THEN
    CREATE ROLE ledger_reader WITH LOGIN;
  END IF;
END
$$;

-- Grant minimal permissions: SELECT and INSERT only
GRANT USAGE ON SCHEMA public TO ledger_reader;
GRANT SELECT ON ledger_entries TO ledger_reader;
GRANT INSERT ON ledger_entries TO ledger_reader;
GRANT USAGE, SELECT ON SEQUENCE ledger_entries_id_seq TO ledger_reader;

-- Explicitly deny UPDATE and DELETE (defense-in-depth)
REVOKE UPDATE, DELETE, TRUNCATE ON ledger_entries FROM ledger_reader;
REVOKE ALL ON SEQUENCE ledger_entries_id_seq FROM ledger_reader;
GRANT USAGE, SELECT ON SEQUENCE ledger_entries_id_seq TO ledger_reader;

-- ============================================================================
-- Setting the ledger_reader password
-- ============================================================================
-- CREATE ROLE above intentionally does not set a password inline (don't want
-- a credential baked into a file that gets committed to version control).
-- Set it out-of-band after applying this file, from the same
-- ICEBERG_LEDGER_RUNTIME_PASSWORD value the app reads at startup:
--
--     python3 set_ledger_reader_password.py
--
-- (reads ICEBERG_LEDGER_RUNTIME_PASSWORD from the environment, connects as
-- the schema-owning superuser, and issues ALTER ROLE ledger_reader WITH
-- PASSWORD ...). Re-run it any time the password rotates.
