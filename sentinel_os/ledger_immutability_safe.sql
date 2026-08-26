-- ============================================================================
-- Ledger Immutability Protection: Append-Only, Tamper-Evident Ledger
-- ============================================================================

-- Block UPDATE operations on ledger_entries
CREATE OR REPLACE FUNCTION block_ledger_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: UPDATE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

-- Create trigger only if table exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'ledger_entries'
    ) THEN
        DROP TRIGGER IF EXISTS prevent_ledger_update ON ledger_entries;
        CREATE TRIGGER prevent_ledger_update
        BEFORE UPDATE ON ledger_entries
        FOR EACH ROW
        EXECUTE FUNCTION block_ledger_update();
    END IF;
END
$$;


-- Block DELETE operations on ledger_entries
CREATE OR REPLACE FUNCTION block_ledger_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is immutable: DELETE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

-- Create trigger only if table exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'ledger_entries'
    ) THEN
        DROP TRIGGER IF EXISTS prevent_ledger_delete ON ledger_entries;
        CREATE TRIGGER prevent_ledger_delete
        BEFORE DELETE ON ledger_entries
        FOR EACH ROW
        EXECUTE FUNCTION block_ledger_delete();
    END IF;
END
$$;
