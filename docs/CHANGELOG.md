# Changelog

Dated, human-readable summary of notable changes. Git history has the
full detail; this is the skim version.

## 2026-09-03

- **Cruft sweep.** Removed `sentinel_os/Deploy/` (5 k8s/argocd files DEPLOYMENT.md
  itself flagged as dead — wrong image/port, deployed `Engines/` workers that no
  longer exist); `DEPLOYMENT.md`'s Kubernetes section rewritten to match.
  `tools/wiring_verify/` (the static "code exists but nothing calls it"
  detector) had rotted since the IVR extraction — its entry-point list and
  acceptance tests still expected `production_harness.py` /
  `api_server_resilient.py` / an `iceberg` compose service, all in GSA-815 now,
  and hardcoded a `~/sentinel_os` path; fixed to current reality (6 passed, was
  1 failed / 3 errors) and the src root is now derived from the tool's own
  location. Corrected stale `Deploy/grafana/` references in `MODEL_CARD.md` and
  `COMPLIANCE.md` (that export is GSA-815's).
- **Ledger operations runbook.** `DEPLOYMENT.md`'s thin "Database" section now
  covers backups (encrypt at rest, key custody separate from the DBA), a
  restore-and-verify drill, a ledger-integrity incident procedure, and a note
  on append-only growth. New `scripts/verify_ledger.py` (a CLI over
  `PostgreSQLLedger.verify_chain()` — the check an auditor runs) and
  `scripts/ledger_backup_verify.sh` (`pg_dump` → restore into a throwaway DB →
  `verify_ledger.py` → drop it); both tested against the live ledger. Also
  fixed two phantom references DEPLOYMENT.md already carried — a `verify_ledger`
  tool and a `/verify` endpoint, neither of which existed; `verify_ledger.py`
  now makes the first one real and the endpoint reference is dropped.
- **Real `SECURITY.md` + a secret-scanning gate.** `SECURITY.md` was still the
  GitHub template (placeholder version table, "tell people how to report..."
  boilerplate); it now carries a real disclosure path, states that `main` is
  the only supported line, and — the substantive part — points at
  `AUDIT_PLAYBOOK.md` and states the operator-trust boundary and the
  twin-not-yet-accepted caveat up front, so a security reviewer sees the honest
  claim without having to go looking. The root README gets a matching Security
  section. New `secrets` CI job runs `gitleaks` (pinned 8.30.1) over the
  working tree as a hard gate; `.gitleaks.toml` carries one justified allowlist
  entry (a fixed fake key in `api_server_v2`'s auth tests). This closes the gap
  #38 opened when it skipped bandit's B105/B106 name heuristic.
- **Dependency CVE audit in CI.** New `deps` job in `tests.yml` runs
  `pip-audit -r requirements.txt` (pinned `2.10.1`) as a hard gate, parallel to
  the suite. Clean today with one justified ignore — `PYSEC-2026-1845` (pytest
  7.4.0's predictable `/tmp/pytest-of-{user}` path: a local-user attack not
  reachable on ephemeral single-user CI; the fix is a pytest 7→9 major bump
  tracked separately). `conservation-kernel` auto-skips (git URL, not PyPI).
- **The GSA-815 boundary is now checked in CI.** `Tests/test_gsa815_contract.py`
  imports every kernel module GSA-815 consumes (the list in
  `GSA-815/DEPENDENCIES.md`, cross-checked against its actual import lines) and
  pins the signatures of the load-bearing call sites (`judge_episode`,
  `GovernanceDecider.safety_check`, `build_governance_call`,
  `PostgreSQLLedger.__init__`). GSA-815 has no CI of its own and is consumed
  purely off PYTHONPATH; before this, a kernel rename broke it silently. Also
  removed two unused pins from `requirements.txt` -- `httpx2==2.7.0` (all HTTP
  code uses `httpx`) and `python-dotenv==1.0.0` (nothing imports `dotenv`; also
  clears PYSEC-2026-2270) -- and corrected the stale `conservation-kernel`
  comment there (it still described the deleted `conservation/gateway.py`).
- **Loader for the 10k synthetic mortgage dataset.** `sentinel_os/run_mortgage_population.py`
  drives `sample_data/mortgage_cassette_synthetic_customers_v2.csv` (committed
  `212d666`, no loader until now) through the full governed path: per row,
  `judge` → governor consult → conservation boundary → hash-chained ledger,
  then `ledger.verify_chain()` over the whole run, then `classify_outcome()`
  over the matured resolutions. On the full 10k: 4,285 governed / 4,286 ledger
  entries / `verify_chain` ok, 0 violations. `sample_data/README.md` documents
  the dataset.
- **Widened the bandit security gate to the whole tree.** Was
  `bandit -r . -x ./Tests -ll` (Medium+ only, `Tests/` unscanned); now
  `bandit -r . -c bandit.yaml` — every file, every severity. The new
  `sentinel_os/bandit.yaml` holds the skip list: each entry was checked to have
  zero findings outside test code (B101 stays live for non-test code via
  `assert_used.skips`), with a one-line rationale. The four checks that still
  fire (B107 ×3, B406 ×1) are false positives / documented local-dev defaults,
  each with an inline `# nosec`. Also corrected two stale claims in
  `.github/workflows/tests.yml`'s own comments (the ruff "zero findings" note
  had been false across several PRs that merged through a red gate; the "17
  Medium bandit findings" count was 4).
- **Removed the pre-transport conservation gateway and cleared the ruff gate.**
  Now that the transport boundary is the governed path (see the entry below),
  the six pre-transport modules (`conservation/{gateway,artifact_factory,
  transformation_factory,artifact_store,types,receipt}.py`) and their two
  isolated test files are deleted — nothing imported them off the hot path.
  Their coverage map is in `conservation/CONFORMANCE.md`; the two DB tests in
  `test_conservation_gateway_security.py` were broken (queried
  `information_schema.triggers`, which omits `TRUNCATE` triggers, and swallowed
  the `AssertionError` as a skip). With them gone plus a sweep of `sage_k/` and
  two stray test imports, `ruff check .` (the CI gate) is at **zero** for the
  first time since it became a hard gate.
- **The conservation boundary is now kernel-conformant.** A governance decision
  is modelled as the transformation it is — `episode (observed record) ->
  judgment` — and verified as such. New `conservation/transport/` (an enforced
  `ConservationGateway`, vendored from `GEMS/transport/`), `episode_source.py`
  (`Episode` -> root source `Artifact`), `judgment.py` (the judgment as one
  rooted `DECISION`/`PROPOSED` proposition), and `boundary.py` (the fail-closed
  entry point). Replaces the pre-transport gateway, which submitted decisions
  as input-less transformations and so rejected every one; ~18 `@requires_pg`
  tests that fail-closed on that now pass. The pre-transport modules stay off
  the hot path pending a cleanup PR. See `conservation/CONFORMANCE.md`.
- **Removed the orphaned IVR/Iceberg simulator support island.** PR #30
  (2026-08-28) extracted the IVR application to GSA-815 but left the
  standalone simulator's `Domain/` `Sim/` `Engines/` `Latent/` `Model/`
  `Training/` `observe/` support tree behind, orphaned once
  `iceberg_complete_simulator.py` itself was gone. All of it (verified
  identical to, or older than, GSA-815's copies) and its three tests
  (`test_rl_learning`, `test_rl_governance_integration`, `test_graph_integrity`
  — the RL ones already in GSA-815), a dead manual runner
  (`test_all_suites.py`), a stale `structure.txt`, and the `Domain.*` import
  shim in `Tests/conftest.py` are removed. `cassettes/ivr_cassette.py` stays
  — it is the kernel's full-capability example and the negative fixture for
  the capability gate. Test collection 706 → 698.
- Docs: reframed `sentinel_os/README.md` (was "Iceberg: Self-Healing IVR
  Platform") to point at the root README as canonical; added
  point-in-time-snapshot banners to the five July-2026 architecture/status
  docs under `docs/`.
- **Pruned three orphans** (follow-up to the island removal):
  `governance/perceive_integration.py` (a Sentinel→PERCEIVE bridge that did
  not import — `governance_orchestrator` is in a sibling repo — and had zero
  importers); `otlp/sentinel-os-pipeline-core.py` + `otlp/telemetry-processor-complete.py`
  (single-line flattened pastes, not valid Python, non-importable filenames,
  ~106 of the repo's ruff findings); and `telemetry_pipeline.py` +
  `Tests/test_telemetry_pipeline.py` (an in-memory call-telemetry collector,
  Iceberg/telephony-shaped, orphaned here — moved to GSA-815, PR #5).
  Collection 698 → 694; ghost_buster baseline 401 → 395.
- **Conservation boundary — three prerequisites, still not conformant.**
  `conservation_kernel` added to `requirements.txt` (git-pin; not on PyPI) —
  it was undeclared, so the two `test_conservation_*` files could not be
  collected and CI never ran the suite. `governance_harness` now passes
  `epistemic_status` as `.value`, not `str(enum)` (the Kernel rejected
  `"EpistemicStatus.ESTIMATED"`). `_map_authority_status` maps recognised
  channels to `PROPOSED`, not an unbacked `HUMAN_AUTHORIZED`/`CANONICAL` (the
  Kernel requires `authorization_refs` for those, which Sentinel does not
  carry down). The gateway is still not Kernel-conformant — it submits
  governed decisions as input-less transformations; `~6 @requires_pg` tests
  fail-close on the verifier. See `sentinel_os/conservation/CONFORMANCE.md`.

## 2026-08-28

- **Extracted the IVR/Iceberg application layer to GSA-815** (PR #30). The
  kernel is domain-blind: `episode`/`event_v1`, the Postgres hash-chained
  ledger + twin + `authorized_by` attestation, the cassette framework,
  `GovernanceHarness`, and the transmission-queue workers stay. The
  telephony harness, Twilio ingestion, Claude governor client,
  queue/staffing/Bayes layer, standalone simulator, and resilient API
  server (12 modules, 22 tests, the `k8s/` Deployment and
  `docker-compose-prod.yml`) moved out. `api_server_v2.py` stays as the
  governed ingress.
- **Persisted observed-event layer** (PR #29): the `EventV1` stream is
  durable and `reconstruct_decision` replays from it.

## 2026-08-27

- **Keyed HMAC attestation for the ledger `authorized_by` claim** (PR #28),
  with key rotation — previous/retired key sets, per-signature key
  fingerprint, `ICEBERG_LEDGER_ATTESTATION_KEY[_FILE]` and
  `..._KEYS_PREVIOUS`/`..._RETIRED` env vars, `ICEBERG_LEDGER_REQUIRE_ATTESTATION`
  enforcement mode.

## 2026-07-24

- **C2 dimension 4: statistical outcome-equity** — the fourth C2
  bias-identification dimension, unbuilt until now, is real: a
  COHORT-level four-fifths-rule disparate-impact checker
  (`regulatory_checks.check_statistical_outcome_equity`), a sealed
  channel for protected-characteristic data completely walled off from
  the live judgment path (`sealed_demographic_channel.py` — new table,
  new role, no grant to `ledger_reader`, ever), and a real BISG
  estimator (`bisg_estimator.py`) reproducing CFPB's own published
  methodology over live Census geocoding/ACS data plus the actual 2010
  Census surname list — never a fabricated estimate; any unreachable
  data source makes the whole estimate INDETERMINATE. `RegulationCheckProfile`
  gains `consent_model` (`opt_in_required` default, or
  `opt_out_permitted`). `CFPBRegBLens.c2_rollup()` can now genuinely
  reach `PASS`, not just `FLAG`/`INDETERMINATE`, when a caller supplies
  an already-computed dimension-4 result for a cohort — automatic
  cohort assembly is not built this session.

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
