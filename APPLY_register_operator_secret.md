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
