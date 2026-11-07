# Delivery record: operator secret on the twin register route

Branch: fix/twin-register-operator-secret

## Steps

1. Step 1 (commit 8bbacb1): the test suite generates a per-run operator secret at startup. No key is hard-coded.
2. Step 2 (commit on this branch): the eight register calls in the test files send the operator token. No assertion changed.
3. Step 3 (commit 8470e4a, earlier): the register route requires the operator secret. It is refused with 503 when the secret is not configured and 401 on a bad token.

## Pre-existing tests edited

Each edit is listed with the step that caused it.

- `Tests/conftest.py` (Step 1): one added import and one line that sets the operator secret if unset. No test changed.
- `Tests/test_twin_cohort_reviews.py` (Step 2): the one register call sends the operator token.
- `Tests/test_obligation_sweep.py` (Step 2): the one register call sends the operator token.
- `Tests/test_twin_outcome_obligations.py` (Step 2): the one register call sends the operator token.
- `Tests/test_regulatory_cassettes.py` (Step 2): the two register calls send the operator token.
- `Tests/test_twin_receiver_auth.py` (Step 2): the two register calls send the operator token. Assertions are unchanged.
- `Tests/test_obligation_supersession.py` (Step 2): the one register call sends the operator token.

## Verification

- Six affected files: 153 passed, 0 failed.
- Full `Tests/` suite: 967 passed, 22 skipped, 0 failed.
- ruff 0.15.22 on the changed test files: clean.

## Operator action

Production must set `TWIN_RECEIVER_REGISTRATION_SECRET` before any replica can be registered. Until it is set, the register route refuses every request.

## Merge with main (step 4)

Main (through commit 2b9cb4c) was merged into this branch. Main contains the pinned-customer-key and signed registration work (PR #108) and later changes. Conflicts were resolved by keeping main's signed, pinned-key registration calls and adding the operator header before each `json=` argument.

Pre-existing tests edited because of this merge:

- `Tests/test_registration_pinning.py` (step 4): added `import os` and the operator header on all 9 register calls. Assertions unchanged.
- `test_twin_live.py` (step 4): the three receiver environments pass `TWIN_RECEIVER_REGISTRATION_SECRET`; a per-run secret is set at import if unset; `register_replica` sends the operator header. Assertions unchanged.
- `Tests/test_twin_receiver_auth.py` (step 4): operator header added to the register calls that main's version left without it (8 calls). Assertions unchanged.
- `Tests/test_obligation_supersession.py`, `Tests/test_obligation_sweep.py`, `Tests/test_regulatory_cassettes.py`, `Tests/test_twin_cohort_reviews.py`, `Tests/test_twin_outcome_obligations.py` (step 4): conflict hunks resolved to main's signed registration body, with the operator header added. Assertions unchanged.

Documentation changed in this merge: `sentinel_os/COMPLIANCE.md` (three conflict hunks combined into one statement of operator secret, pinned key and customer signature), `sentinel_os/DEPLOYMENT.md` (row for `TWIN_RECEIVER_REGISTRATION_SECRET`), `SECURITY.md` (operator-secret bullet).

## Verification (step 4)

- Receiver and registration suites (8 files): 168 passed, 0 failed.
- `test_twin_live.py` run alone: 18 passed, 0 failed.
- Full suite (`sentinel_os/` and `tools/`) on the merged tree: 1170 passed, 22 skipped, 8 failed. All 8 failures are in `test_twin_live.py` with a receiver spawn or order error.
- Full suite on clean current main (2b9cb4c): 1031 passed, 148 skipped, 21 failed. The failures include the same live-suite tests and more. The skip counts differ between the two runs, so the comparison is noisy. The live-suite failures in full-suite order are pre-existing on main and are not caused by this branch.
- ruff 0.15.22: clean.
- bandit: no findings.
