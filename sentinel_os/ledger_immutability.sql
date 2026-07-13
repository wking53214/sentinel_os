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
REVOKE UPDATE, DELETE ON ledger_entries FROM ledger_reader;
REVOKE ALL ON SEQUENCE ledger_entries_id_seq FROM ledger_reader;
GRANT USAGE, SELECT ON SEQUENCE ledger_entries_id_seq TO ledger_reader;
