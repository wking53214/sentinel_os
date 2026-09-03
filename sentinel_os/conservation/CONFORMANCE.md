# Conservation boundary — conformance status

`governance_harness._write_decision` calls `conservation.boundary.verify_governed_decision(episode, record)`
before persisting to the ledger and fail-closes on anything other than a clean
acceptance ("no durable state without conservation verification").

## Conformant (2026-09-03)

A governance decision is modelled as a **conservation transformation**:
`episode (the observed record) -> judgment`.

- `conservation/transport/` — an enforced `ConservationGateway` around
  `conservation_kernel` (register-root + verify-transformation + fail-closed
  choke point), vendored near-verbatim from `GEMS/transport/` where its
  20-attack hostile corpus blocks all 20. See `transport/PROVENANCE.md`.
- `conservation/episode_source.py` — `Episode` -> a root source `Artifact`:
  `actual` -> `OBSERVATION` (external origin), `requested` -> `ASSUMPTION`,
  `actor_report` -> `INFERENCE` (machine origin, `Uncertainty`,
  `derivation_method`) — never `FACT`/`OBSERVATION`, the same distinction
  `Episode` itself enforces via `discrepancies`; `outcome_reasons` ->
  `OBSERVATION`.
- `conservation/judgment.py` — the judgment: one new `DECISION`-status,
  `MACHINE_ORIGINATED`, `PROPOSED`-authority proposition rooted to the observed
  facts. `BaseGem.declared_changes_for` / `_proposal` do the canonical-JSON
  hash alignment and per-dimension declared-change diffing.
- `conservation/boundary.py` — a fresh gateway per call. The kernel is a
  **stateless verifier + choke point** here, not an accumulating ledger:
  Postgres stays the durable ledger; reconstruction stays Sentinel's own
  event-sourced path.

The kernel accepts an honest judgment (`PASS_WITH_DECLARED_TRANSFORMATION`) and
rejects: judgments claiming human-originated facts (`FALSE_HUMAN_ATTRIBUTION`),
unrooted claims (`UNROOTED_NEW_PROPOSITION`), unbacked human authority
(rejected at construction). All ~18 `@requires_pg` tests that previously
fail-closed on the pre-transport gateway now pass.

## Deferred

- **The keyed `authorized_by` attestation is not threaded through** as
  `authorization_refs`. Not on the critical path — a machine judgment at
  `DECISION`/`PROPOSED` passes cleanly. It is only needed the day a judgment
  should legitimately claim `HUMAN_AUTHORIZED` (a genuine human sign-off). At
  that point `judgment.py` gets an `AuthorityReference` and
  `episode_source.py` / the ledger's PR-#28 attestation feed it.

## To be removed (follow-up)

The pre-transport modules are OFF the governed hot path and kept only so their
own isolated tests keep passing until a dedicated cleanup PR:

- `gateway.py`, `artifact_factory.py`, `transformation_factory.py`,
  `artifact_store.py`, `types.py`, `receipt.py`
- `test_conservation_integration.py`, `test_conservation_gateway_security.py`

Their coverage is re-expressed against the transport path in
`Tests/test_conservation_boundary.py`.
