-- ============================================================================
-- Sealed Demographic Channel: protected-characteristic data, walled off
-- from the live decision path
-- ============================================================================
--
-- C2 dimension 4 (statistical outcome-equity) needs estimated or
-- self-reported protected-characteristic data (race/ethnicity) to compare
-- outcomes across groups. That data must NEVER be reachable from the live
-- judgment path (episode.py / cassette judge()/explain()) -- the same
-- posture episode.py already holds for actor_report (recorded, never read
-- by judgment). This file is the structural half of that wall: a table
-- completely separate from ledger_entries, and a role that can write to
-- and read from ONLY this table -- never granted to the runtime identity
-- (ledger_reader / ICEBERG_LEDGER_RUNTIME_USER) the live judgment path
-- actually connects as.
--
-- Mirrors ledger_immutability.sql's ledger_reader pattern exactly: a
-- dedicated, minimally-privileged role, explicit grants, explicit
-- REVOKEs as defense-in-depth. The two roles are deliberately never the
-- same and never granted to each other -- an app process authenticated
-- as ledger_reader has no path to this table at all, not even by
-- accident, because there is no grant to revoke discovery of: it was
-- simply never given.

CREATE TABLE IF NOT EXISTS protected_characteristic_estimates (
    id SERIAL PRIMARY KEY,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- The subject this estimate is ABOUT -- an episode_id or ledger
    -- decision id, kept as a plain string so either shape fits without a
    -- foreign key into ledger_entries (deliberately no FK: this table
    -- must be independently droppable/restrictable without touching the
    -- ledger's own schema, and a join path back into ledger_entries is
    -- exactly the kind of connective tissue that would make "never
    -- touches the live decision" harder to audit).
    subject_id VARCHAR(200) NOT NULL,
    cohort_key VARCHAR(200),
    -- 'self_reported' (opt_out_permitted default, or opt_in_required's
    -- voluntary supplement) or 'bisg_estimated' (opt_in_required
    -- default, or opt_out_permitted's decline fallback). Never
    -- 'observed'/'inferred-from-judgment' -- this table has no
    -- legitimate path to data that ever touched a decision.
    source VARCHAR(20) NOT NULL,
    -- Estimated/reported protected-characteristic distribution, e.g.
    -- {"white": 0.7, "black": 0.1, "api": 0.05, "aian": 0.02,
    --  "multiracial": 0.03, "hispanic": 0.1} for a BISG posterior, or a
    -- single-category self-report. JSONB, not fixed columns: the
    -- category vocabulary is a checker/profile concern, not a schema one.
    estimate JSONB NOT NULL,
    -- Free-form provenance (e.g. which BISGEstimator implementation and
    -- version produced this, or "customer self-report via <form>") --
    -- an audit trail for the estimate itself, distinct from the ledger's
    -- own audit trail for decisions.
    method VARCHAR(200),
    CHECK (source IN ('self_reported', 'bisg_estimated'))
);
CREATE INDEX IF NOT EXISTS idx_pce_subject ON protected_characteristic_estimates(subject_id);
CREATE INDEX IF NOT EXISTS idx_pce_cohort ON protected_characteristic_estimates(cohort_key);

-- ============================================================================
-- Append-only, same bar as ledger_entries (ledger_immutability.sql)
-- ============================================================================
-- Protected-characteristic data is at least as sensitive as governance
-- decisions -- an estimate is corrected by recording a NEW one, never by
-- rewriting or erasing what was estimated/reported before. These are
-- table-level triggers (fire for ANY role, not just sealed_channel_writer),
-- the same belt beyond the role-level REVOKEs below as suspenders.

CREATE OR REPLACE FUNCTION block_pce_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'protected_characteristic_estimates is append-only: UPDATE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_pce_update ON protected_characteristic_estimates;
CREATE TRIGGER prevent_pce_update
BEFORE UPDATE ON protected_characteristic_estimates
FOR EACH ROW
EXECUTE FUNCTION block_pce_update();

CREATE OR REPLACE FUNCTION block_pce_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'protected_characteristic_estimates is immutable: DELETE not permitted on row id=%', OLD.id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_pce_delete ON protected_characteristic_estimates;
CREATE TRIGGER prevent_pce_delete
BEFORE DELETE ON protected_characteristic_estimates
FOR EACH ROW
EXECUTE FUNCTION block_pce_delete();

CREATE OR REPLACE FUNCTION block_pce_truncate()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'protected_characteristic_estimates is append-only: TRUNCATE not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS prevent_pce_truncate ON protected_characteristic_estimates;
CREATE TRIGGER prevent_pce_truncate
BEFORE TRUNCATE ON protected_characteristic_estimates
FOR EACH STATEMENT
EXECUTE FUNCTION block_pce_truncate();

-- ============================================================================
-- The sealed-channel role: INSERT + SELECT on this table ONLY
-- ============================================================================
-- Deliberately NOT granted USAGE on anything else, and deliberately NOT
-- the same role as ledger_reader. A process authenticated as
-- sealed_channel_writer cannot read ledger_entries; a process
-- authenticated as ledger_reader (the live judgment path's own identity)
-- cannot read this table. Two roles, two purposes, no overlap -- the
-- overlap is exactly what "never touches the live decision" would mean
-- giving up.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sealed_channel_writer') THEN
    CREATE ROLE sealed_channel_writer WITH LOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO sealed_channel_writer;
GRANT SELECT, INSERT ON protected_characteristic_estimates TO sealed_channel_writer;
GRANT USAGE, SELECT ON SEQUENCE protected_characteristic_estimates_id_seq TO sealed_channel_writer;

-- Defense-in-depth, same posture as ledger_reader: no UPDATE/DELETE, even
-- though the granular GRANT above never offered them. A protected-
-- characteristic estimate is corrected by inserting a new row (source
-- data updates rarely and an audit trail of what was estimated when is
-- itself worth keeping), not by mutating history.
REVOKE UPDATE, DELETE, TRUNCATE ON protected_characteristic_estimates FROM sealed_channel_writer;

-- Explicit, load-bearing negative grant: confirms ledger_reader (if it
-- already exists when this file is applied) has no access to this table.
-- A no-op if ledger_reader was never granted anything here in the first
-- place -- which is exactly the point; this statement exists to make
-- that fact checkable/re-assertable, not to remove a grant this file
-- itself never gave.
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_reader') THEN
    REVOKE ALL ON protected_characteristic_estimates FROM ledger_reader;
  END IF;
END
$$;

-- ============================================================================
-- Setting the sealed_channel_writer password
-- ============================================================================
-- Same convention as ledger_reader (see ledger_immutability.sql): no
-- password set inline here. PostgreSQLLedger self-provisions
-- ledger_reader's password from ICEBERG_LEDGER_RUNTIME_PASSWORD at every
-- startup; SealedDemographicChannel does the identical thing for
-- sealed_channel_writer from SEALED_CHANNEL_PASSWORD, in
-- sealed_demographic_channel.py.
