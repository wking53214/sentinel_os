# Delivery record: default-password sweep and the forgeable audit log

Branch: claude/all-repo-t0sxey (continues Step 1, recorded in `APPLY_export_ledger_password.md`)

## Steps

2. Step 2 (commit 77d2b73): runtime code no longer falls back to the well-known database password `iceberg`. Each place below now refuses when no password is given, the same rule `sentinel_worker.py` and `scripts/verify_ledger.py` already follow. Host, port, database and user keep their existing defaults.
   - `sentinel_os/governance_harness.py` (`_connect_ledger`): with `postgres_host` set and no `postgres_password`, construction raises "postgres_host is set but postgres_password is not". The check runs before the connection attempt, so it is raised even with `require_cassette_binding=False` instead of being turned into an offline run.
   - `sentinel_os/governance/ledger_postgres.py` (`PostgreSQLLedger.__init__`): the owner password defaulted to `iceberg`. It now has no default and the constructor refuses without one. The `# nosec B107` marker on that line is gone because there is no hard-coded default left for bandit to flag.
   - `sentinel_os/sealed_demographic_channel.py` (`SealedDemographicChannel.__init__`): same change as the ledger.
   - `sentinel_os/human_review_ledger.py`: the ledger behind the two human-review routes was built as `PostgreSQLLedger()`, so it relied on the default above. It now reads `POSTGRES_PASSWORD` and refuses with "POSTGRES_PASSWORD is not set" when it is unset.
   - `sentinel_os/obligation_sweep.py`: `--sealed-channel-password` defaulted to `iceberg`. It now falls back to `POSTGRES_PASSWORD`, and with neither set `SealedDemographicChannel` refuses to start.
   - `sentinel_os/COMPLIANCE.md` re-stamped to `main` @ `cf4bbc1` (it names three of the files above, so the doc-freshness gate requires it). `sentinel_os/DEPLOYMENT.md` lists the new refusals.
3. Step 3 (commit 43aae72): `fortress_audit.log` (sage_k experiment output, 480 HMAC-signed records, about 154 KB) is no longer tracked. It was committed although `.gitignore` ignores it, and 360 of its records verify with the published former default key `development-key`, so they can be forged. Nothing reads it: no test, script or document refers to the file, and `sage_k/kernel.py` only appends to a file of that name in its working directory when `FORTRESS_AUDIT_LOG` is unset. The `.gitignore` entry keeps new local copies out.

## Swept and left unchanged

- `sentinel_os/run_mortgage_population.py`: a local demo runner that resets the ledger table. It names the throwaway local database's credential directly (documented in its docstring as needing `iceberg/iceberg@localhost`), the same way the test fixtures do. It is not a fallback, so Step 2 does not change it.
- `sentinel_os/sage_k/kernel.py`: already has no default key. `development-key` appears only so production can refuse it and other environments can warn about it.
- `sentinel_os/twin_shipper.py` (`POSTGRES_PASSWORD` defaults to an empty string, which is no credential), `api_key_auth.py` and `twin_receiver.py` (empty defaults that switch a feature off or refuse): no well-known value.
- Test files, `conftest.py` files and the CI workflow keep the throwaway database's `iceberg` credential.

## Pre-existing tests edited

None.

## Verification

Postgres 16 and Redis set up as `.github/workflows/tests.yml` does (role and database `iceberg`, `twin_ensure_services`, an ephemeral TLS certificate), with `POSTGRES_PASSWORD` unset in the calling shell.

- Baseline (before Step 2): 1201 passed, 17 failed, 22 skipped. The 17 failures come from this machine, not the code: 16 are `test_twin_live.py` tests whose services, run as other OS users, cannot execute an interpreter under the private scratch directory, and `tools/test_doc_freshness.py::test_claimed_file_changed_after_doc_is_stale` fails because this machine's global git config signs commits. It passes with a neutral git config.
- After Step 2: 1213 passed, 5 failed (all `test_twin_live.py`, the same cause), 22 skipped. With the scratch directory kept traversable, `test_twin_live.py` passed 17 of 18, and the remaining test passed on its own rerun.
- After Step 3: the `sage_k` tests pass (19).
- Final run of every test directory (`sentinel_os/` and `tools/`) at the Step 3 commit, with the scratch directory kept traversable: 1218 passed, 0 failed, 22 skipped.
- `ruff==0.15.22 check .`, `bandit -r . -c bandit.yaml` and `tools/doc_freshness.py` (FRESH) pass. `gitleaks dir` 8.30.1 finds no leaks in a clean export of the tree. `pip-audit` 2.10.1 reports no known vulnerabilities.
- With no password, each changed entry point refuses with the message above, and a harness with no `postgres_host` still runs offline.
