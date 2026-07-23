# Changelog

Dated, human-readable summary of notable changes. Git history has the
full detail; this is the skim version.

## 2026-07-23

- **Cassette kernel/capability split** — the cassette contract is no
  longer IVR-shaped. A minimal domain-blind KERNEL (identity, typed
  parameter declarations, `judge(episode)` / `explain(episode)`) plus
  four opt-in CAPABILITY modules (`telephony_ingest`,
  `routing_topology`, `rl`, `self_healing`), each owning its own
  parameters and methods. A cassette declares a `CAPABILITIES`
  manifest; load-time validation checks kernel + the union of enabled
  capabilities, and **rejects any parameter owned by a disabled
  capability** — the anti-placeholder rule. Schema `2.0.0`; snapshots
  now record the manifest.
- **Episode ground-truth record** (`episode.py`) — kernel-level record
  of requested vs. actual outcome with two enforced invariants: a
  reason is owed on ANY outcome mismatch (paid-but-reduced counts,
  not just formal denials), and the actor's self-report is always
  cross-checked against the observed record (twin posture), with
  divergences surfaced ahead of the cassette's own factors in every
  explanation. No judgment path admits an unvalidated episode.
- **Banking cassette is honest now** — declares
  routing + rl + self_healing only; the three flagged placeholder
  `twilio_*` thresholds are gone (validation would now refuse them),
  and its judgment moved to the kernel surface with arithmetic
  unchanged. Consequence: banking is refused by the telephony
  pipelines at the door (legible capability error at construction)
  instead of pretending Twilio-readiness it never had.
- **IVR is the reference implementation** — enables all four
  capabilities; kernel `judge` proven arithmetically identical to the
  legacy `score_outcome_quality` by an equivalence sweep. Version
  `2.0.0` (identity, not behavior: the code hash changed, and binding
  enforcement correctly refuses a changed hash under an old version).
- **Engines guard their doors** — `SentinelCore`, `CassetteHarness`,
  `IcebergProductionHarness` (construction and swap), and Twilio
  ingest each refuse a cassette missing the capabilities they read,
  at construction rather than mid-call.
- **Pre-existing defect fixed** — `serialize_cassette_for_ledger`
  duplicated the snapshot serialization and had silently drifted from
  `GovernanceParameters.snapshot()`; it now delegates to the single
  source of truth.
- Full suite: 307 passed (279 baseline + 28 new proof tests, including
  a kernel-only cassette with zero telephony surface that loads,
  validates, and judges — the shape a hiring cassette starts from).

## 2026-07-22

- **Phase 2 merged** — closed 6 of 7 Known Limitations: cassette
  version binding, code-hash coverage, structural injection defense,
  model identity per decision, decision supersession, authorizing
  identity. See `COMPLIANCE.md` and `PHASE2_MIGRATION_NOTES.md`.
- **ICEBERG_LEDGER_RUNTIME_USER made fail-closed** — the app no longer
  boots with a privileged database credential, even by accident. See
  `governance/README.md`.
- **docker-compose fixed end-to-end** — the runtime-user fix above
  would have broken `docker-compose up` (no fallback credential to
  silently use); fixed via self-provisioning instead of just patching
  the compose file. Also fixed a separate, pre-existing startup race
  (`iceberg-main` could start before Postgres was actually ready to
  accept connections).
- **CI corrected** — `tests.yml` previously only ran the `Tests/`
  subdirectory (27 of 37 test files) and had no Redis service at all.
  Now runs the full suite; `test_twin_live.py` is explicitly excluded
  (needs infrastructure — 3 OS identities, real TLS PKI between them —
  not yet reconstructed in CI) rather than silently skipped.
- **Full stack verified live** — real Postgres ledger, real fail-closed
  credential behavior, a governed call correctly blocked with no
  governor configured, and an independently-verified 25-entry hash
  chain, all confirmed running end-to-end.
