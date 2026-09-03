# Conservation boundary — conformance status

`governance_harness._write_decision` routes every governed decision through
`conservation/gateway.py` and fail-closes the ledger write if the Conservation
Kernel does not accept it ("no durable state without conservation
verification").

## Done (2026-09-03)

- **`conservation_kernel` is a declared dependency** (`requirements.txt`,
  git-pinned; not on PyPI). Before this, the two `test_conservation_*` files
  could not be collected and the whole CI run aborted — so the ~890-test suite
  never ran.
- **Epistemic status is passed correctly** — `governance_harness` passed
  `str(EpistemicStatus.ESTIMATED)` (`"EpistemicStatus.ESTIMATED"`), which the
  Kernel rejects; now `.value` (`"estimated"`).
- **Authority claims are honest** — `_map_authority_status` mapped every
  recognised channel to `HUMAN_AUTHORIZED` / `CANONICAL`, both of which the
  Kernel requires `authorization_refs` for, which Sentinel does not carry down
  here. Now they map to `PROPOSED` ("produced through a known channel, put
  forward for the ledger, not a substantiated human sign-off"). Unrecognised
  strings still fail closed to `NONE`.

## Not done — the gateway is not Kernel-conformant

The gateway submits a governance decision as a **transformation with no input
artifact** (`kernel.submit(input_artifacts=(), ...)`). A governance decision
is `episode (observed events + requested outcome) -> judgment`; the Kernel's
verifier rejects the current shape with:

| Violation | Cause |
|---|---|
| `NO_INPUT_ARTIFACT` | no input artifact identified |
| `OUTPUT_HASH_MISMATCH` | gateway hashes with `json.dumps`; Kernel recomputes with `canonical_json` |
| `UNDECLARED_CHANGE` | a protected dimension changed without a `DeclaredChange` |
| `UNROOTED_NEW_PROPOSITION` | output proposition has no parent and no external source |
| `FALSE_HUMAN_ATTRIBUTION` | `origin=HUMAN_ORIGINATED` on content a `SYSTEM` actor produced |

Consequence: every `@requires_pg` test that persists a governed decision
(`test_governance_harness`, `test_governance_harness_outcome_obligation`,
`test_governance_harness_regulatory_wiring`, `test_governance_harness_stress`,
`test_critical_integration`, ...) fail-closes on this. Those tests **also fail
on `main` today** — via `No module named 'conservation_kernel'` — they were
just hidden behind the collection abort.

## Fix direction

Model the decision as a real transformation: register the episode as a root
source artifact, submit the judgment as its transformation, use a machine
transformer + `MACHINE_ORIGINATED` origin, declare the changed dimensions,
align hashing with the Kernel, and thread the keyed `authorized_by`
attestation (governance ledger, PR #28) through as `authorization_refs` so a
genuine human sign-off can legitimately reach `HUMAN_AUTHORIZED`.

**`gems_transport` (`GEMS/transport/`) already implements this** — an enforced
`ConservationGateway` (register-root + verify-transformation + fail-closed
choke point) with a 20-attack hostile corpus, all 20 blocked. It is the
candidate to extract and wire in rather than rebuild. `tie_adapter.py` there
is a deliberate stub for exactly the source-ingestion adapter Sentinel would
write (`Episode -> Artifact`).
