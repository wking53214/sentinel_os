# Provenance: HERALD adversarial campaign, 2026-08-26

`HERALD_CAMPAIGN_2026-08-26.md` is the results record from the branch
`herald-adversarial-campaign-2026-08-26` (tip `29e893386e`), copied
verbatim. The branch was closed unmerged on 2026-09-11 and this file is
why.

## What the branch was

A snapshot of `main` as of 2026-08-26, before the IVR/Iceberg extraction
(#30) and the Deploy/ removal (#44), plus nine commits (2026-08-23 to
08-26) that built `sentinel_os/conservation/gateway.py` with a typed
actor model (`types.py`, `artifact_factory.py`, `artifact_store.py`,
`transformation_factory.py`), a 12-scenario adversarial test
(`test_herald_adversarial.py`), and the results record. The campaign
found three CRITICAL weaknesses in that gateway's own artifact model:
a content hash computed once over a mutable dict, unfrozen metadata that
allowed in-memory authority escalation, and a third of the same family.
The final commit applied countermeasures.

## Why it was not merged

Between 2026-09-03 and 09-08 (#34, #35, #36, #45) `main` removed that
gateway entirely and replaced it with `conservation/transport/`,
vendored from GEMS with its own `PROVENANCE.md`: every artifact and
contract type is `@dataclass(frozen=True)`, and it carries a 20-attack
hostile corpus, all blocked. The three findings are properties of code
that no longer exists on `main`; the replacement was designed against
exactly that class of attack. Merging the branch would have resurrected
both the IVR island and the rejected gateway.

## What is kept

The results record, as dated evidence that this attack class was found
by the project's own red team before the redesign. The 12 scenarios
below are the checklist for confirming the transport layer's corpus
covers them; that check has not been done and is noted as open.

- test_artifact_creation_without_conservation_submission
- test_transformation_record_without_authorization_refs
- test_decision_without_artifact_creation
- test_artifact_content_mutation_after_storage
- test_metadata_mutation_authority_escalation
- test_actor_kind_inference_via_substring
- test_authority_whitelist_bypass
- test_actor_identity_fabrication
- test_system_actor_as_model_escalation
- test_authorized_by_spoofing_with_valid_format
- test_gateway_rejection_prevents_ledger_write
- test_multiple_decision_writes_produce_unique_actors
