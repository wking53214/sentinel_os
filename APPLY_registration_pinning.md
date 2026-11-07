# Delivery record: operator-pinned customer key for replica registration

Branch: feat/pinned-customer-key

## Steps

1. Step 1: shared helper defining what a customer signs at registration (replica id plus the registration fields, including the read token), with sign, verify and builder helpers. Tests: Tests/test_registration_signature.py.
2. Step 2: the receiver refuses registration unless the replica is on the operator's pinned list, the body's customer key matches the pinned key, and the registration signature verifies with that key. With no pinned list, every registration is refused. Tests: Tests/test_registration_pinning.py.
3. Step 3: the receiver rereads the operator's pinned key file on every registration, so adding a customer needs no restart. A missing or unreadable file fails closed. Tests added to Tests/test_registration_pinning.py.
4. Step 4: the live test (test_twin_live.py) pins each replica's customer key in the file the receivers read, signs each registration, and passes the file path to every receiver it starts. This step could not be run here: the live test needs the CI service users and twin_ensure_services. CI is the check for it.

## Pre-existing tests edited

Setup only. No assertion changed. Each file registers its test replica with a generated key and a signature, and builds the app with that replica pinned. Listed with the step that caused it.

- `Tests/test_twin_receiver_auth.py` (Step 2): the `twin` fixture registers a signed replica. The test that registers a second replica builds its own pinned client and reads through it. Assertions unchanged.
- `Tests/test_twin_cohort_reviews.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_obligation_sweep.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_twin_outcome_obligations.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_obligation_supersession.py` (Step 2): fixture registration signed and pinned.
- `Tests/test_regulatory_cassettes.py` (Step 2): two fixtures registration signed and pinned.
- `test_twin_live.py` (Step 4): registration helper pins and signs; receivers started with the pinned-file path. Assertions unchanged.

## Not changed

- Existing replicas registered before this change keep their stored credentials. Only new registrations are checked.
- A replica that is not on the pinned list cannot be created by any route.
