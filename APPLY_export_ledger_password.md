# Delivery record: export_ledger.py requires POSTGRES_PASSWORD

Branch: claude/all-repo-t0sxey

## Steps

1. Step 1 (commit edcf845): `sentinel_os/scripts/export_ledger.py` no longer falls back to the well-known password `iceberg` when `POSTGRES_PASSWORD` is unset. It exits with "POSTGRES_PASSWORD is not set", the same rule `scripts/verify_ledger.py` already follows. No other behaviour changes: host, port, database and user keep their existing defaults.

## Pre-existing tests edited

Each edit is listed with the step that caused it.

- `Tests/test_verdict_receipts.py` (Step 1): the `_export_to` helper runs the script as a subprocess and relied on the removed fallback to reach the throwaway test database. It now passes `POSTGRES_PASSWORD` in the subprocess environment (the test database's local credential, as the other database fixtures in this suite use), unless the caller's environment already sets one. No assertion changed.

## Verification

- `Tests/test_verdict_receipts.py` against a local Postgres 16 set up as CI does (role and database `iceberg`): 40 passed, 0 skipped, with `POSTGRES_PASSWORD` unset in the calling shell.
- Running the script with `POSTGRES_PASSWORD` unset exits with the error above.
- `ruff==0.15.22 check`, `bandit -r . -c bandit.yaml` and `tools/doc_freshness.py` (FRESH) pass.

## Not changed

- Test fixtures and the CI workflow keep the throwaway database's `iceberg/iceberg` credential.
