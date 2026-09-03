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
(rejected at construction). The 18 `@requires_pg` tests that previously
fail-closed on the pre-transport gateway now pass (PR #35: CI 855→881 passed,
0 failed).

## Not bridged, by design: `authorized_by` attestation ≠ kernel authority

The keyed `authorized_by` attestation (PR #28) is **not** threaded into the
transformation as an `authorization_refs` / `AuthorityReference`. The judgment
is fixed at `MACHINE_ORIGINATED` origin and `PROPOSED` authority regardless of
what `record.authorized_by` says. This is a deliberate boundary, not
unfinished work.

The kernel's `HUMAN_AUTHORIZED` — and every authority status above
`PROPOSED` — asserts that **a specific authorization event happened**; the
kernel requires an `AuthorityReference` pointing at that event. The
`authorized_by` attestation proves something different and weaker: that a
named string was written by a component holding the service signing key and
has not changed since (`governance/ledger_postgres.py` documents this scope
inline, on the `authorized_by` field). One shared key, any holder
indistinguishable from any other, a leaked key forges it — it establishes
writer integrity, not that the named party authorized anything. Mapping it
onto a kernel authority status would let key-holder integrity masquerade as a
human sign-off — the exact false attribution the kernel exists to reject.

Nor is there a code path that could exercise such a mapping today.
`verify_governed_decision` is reached only from
`governance_harness._write_decision`, which builds its
`GovernanceDecisionRecord` with `authorized_by=None`. The other writers that
do set `authorized_by` (`regulatory_deck.py`, `contract_egress.py`) produce
different record kinds that never enter the conservation boundary.

The day a judgment should legitimately claim `HUMAN_AUTHORIZED`, the
requirement is a real registered authorization event feeding an
`AuthorityReference` — `conservation/transport/` already carries the type and
`builder._proposal`'s `authority_refs=` parameter — not a re-interpretation of
the attestation string. `Tests/test_conservation_boundary.py::test_authorized_by_string_cannot_raise_kernel_authority`
pins the invariant: the judgment stays `MACHINE_ORIGINATED` / `PROPOSED` for
every spoofed `authorized_by` value.

## Pre-transport modules removed (2026-09-03)

The pre-transport gateway and its factories/store/types/receipt
(`gateway.py`, `artifact_factory.py`, `transformation_factory.py`,
`artifact_store.py`, `types.py`, `receipt.py`) and their two isolated test
files (`test_conservation_integration.py`, `test_conservation_gateway_security.py`)
have been deleted. Nothing imported them off the transport path.

Their coverage is re-expressed against the transport path in
`Tests/test_conservation_boundary.py`:

- gateway wired into `_write_decision` (was A3) — `test_boundary_is_wired_into_write_decision`
- fail-closed on kernel rejection (was A4) — `test_verify_propagates_the_real_rejection`,
  `test_unrooted_judgment_is_rejected`
- `authorized_by` string content cannot buy kernel authority (was A1/A2) —
  `test_authorized_by_string_cannot_raise_kernel_authority`
- ledger immutability triggers (was A6) — covered by `Tests/test_ledger_boot_lock.py`
  (existence + auto-restore). The two A6 tests here were broken: they queried
  `information_schema.triggers` (which omits `TRUNCATE` triggers) and swallowed
  the resulting `AssertionError` as a skip, so they never ran a passing
  assertion. All three triggers do exist (`pg_trigger`), and `ledger_reader`
  `UPDATE` is permission-denied.
