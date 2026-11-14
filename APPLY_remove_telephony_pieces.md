# Delivery record: remove friction_core and recommendation_impact from the kernel

Branch `refactor/remove-telephony-pieces`. Goal: the domain-blind kernel stops owning telephony pieces. GSA-815 PR 42 carries its own copies and must merge first.

## Steps
1. Removed `governance/friction_core.py`.
2. Removed `recommendation_impact.py` and `Tests/test_recommendation_impact.py` (moved to GSA-815).
3. This note.

## Existing tests edited
- `Tests/test_gsa815_contract.py` (step 1): the `governance.friction_core` entry removed from the contract list. That entry asserted exactly the behaviour step 1 changes. Nothing else in the file changed.

## Existing tests removed
- `Tests/test_recommendation_impact.py` (step 2): moved with its module; 12 tests now run in GSA-815.

## Left alone on purpose
- `TelephonyIngest`, `ivr_cassette.py` and about a dozen kernel tests that use the IVR cassette as a fixture. Removing them needs those tests re-pointed at a neutral fixture; that is a separate change.
- Comments naming the removed files in `governance/ledger_postgres.py` and `governance_harness.py`. Editing them would touch files with other work in flight and trip the COMPLIANCE.md freshness gate for a comment.
- `.ghost_baseline.json` entries for the removed files; no CI gate reads them.
