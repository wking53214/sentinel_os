# Delivery record: operator-pinned customer key for replica registration

Branch: feat/pinned-customer-key

## Steps

1. Step 1: shared helper defining what a customer signs at registration (replica id plus the registration fields, including the read token), with sign and verify helpers and a builder for signed bodies. Tests: Tests/test_registration_signature.py.
2. Step 2: the receiver refuses registration unless the replica is on the operator's pinned list, the body's customer key matches the pinned key, and the registration signature verifies with that key. If no pinned list is configured, every registration is refused. Tests: Tests/test_registration_pinning.py.
3. Step 3: live test (test_twin_live.py) registers with signed bodies and writes the pinned list for the receiver it starts. Not runnable in this environment; CI is the check.
4. Step 4: operator documentation for the pinned list.

## Pre-existing tests edited

Setup only. No assertion changed. Each file registers its test replica with a generated key and a signature, and builds the app with that replica pinned. Listed with the step that caused it.

- `Tests/test_twin_receiver_auth.py` (Step 2): the `twin` fixture registers a signed replica. The test that registers a second replica builds its own pinned client and reads through it. Assertions unchanged.
- `Tests/test_twin_cohort_reviews.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_obligation_sweep.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_twin_outcome_obligations.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_obligation_supersession.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_regulatory_cassettes.py` (Step 2): two fixtures registration signed and pinned.

## Not changed

- Existing replicas registered before this change keep their stored credentials. Only new registrations are checked.
- A replica that is not on the pinned list cannot be created by any route.
