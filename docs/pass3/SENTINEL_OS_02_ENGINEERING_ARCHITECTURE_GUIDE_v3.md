> **⚠ Point-in-time snapshot — not maintained.** Documented baseline `68cadfb` (July 29, 2026). Much has landed since — PRs #28–#45 as of 2026-09-03: the keyed `authorized_by` ledger attestation with key rotation, the persisted observed-event layer, the **extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo** (2026-08-28) — its standalone simulator, `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree, Twilio ingestion, Claude governor client, and queue/staffing/Bayes layer are no longer in this repo — the mandatory `conservation/` boundary, and a widened CI gate set. Treat every directory map, module inventory, test count, and CI description below as historical. For current state see the [repository root README](../../README.md) and **[POST_SNAPSHOT_CORRECTIONS_2026-09-03.md](./POST_SNAPSHOT_CORRECTIONS_2026-09-03.md)**, which reconciles the specific quantitative claims (test totals, the security-scan framing, the mortgage-cassette-in-CI question) a present-day reader would otherwise take as current.

---

# DOCUMENT 2 — ENGINEERING ARCHITECTURE GUIDE

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification:** `FACT` = stated in a source document · `DERIVED` = follows from two or more documented facts · `INTERPRETATION` = reasonable reading, not established · `UNKNOWN` = not in the sources.

Only mechanisms that exist in the documented repository are described. Where a procedure a new engineer would expect is absent from the sources, it is marked `UNKNOWN` rather than supplied.

---

## 0. READER FRAME

**AUDIENCE**
A senior software engineer who has just been given commit access to this repository and is expected to contribute.

**READER QUESTIONS**
1. What are the load-bearing parts, and which parts are scaffolding?
2. What will break in a way I won't notice if I touch the wrong thing?
3. What rules were decided deliberately, so I don't "fix" them?
4. Where does state live, and what can I safely change about it?
5. How do I add something without reopening the audited core?
6. What does the test suite actually prove?

**DECISION OBJECTIVE**
Decide what to read, what to leave alone, and what to ask about before opening a first pull request.

**TRUST FAILURE**
This reader loses confidence, and may cause real damage, if the following are not stated plainly:

- `FACT` Editing a governance module changes its code hash. Recorded decisions are bound to that hash. A mismatch produces a version-conflict error whose documented remedy is to drop the ledger table and rebuild from the twin. A routine refactor can therefore invalidate stored evidence.
- `FACT` The ledger is append-only at the database level and the runtime role holds no UPDATE or DELETE permission. Ordinary migration habits do not apply and no migration procedure for this table is documented.
- `FACT` A set of design decisions is labeled in the sources as locked and not to be relitigated. Several look like defects if encountered without that context — most notably that the governance verdict deliberately controls nothing.
- `FACT` The four source maps contradict each other on several structural points, listed in §9. There is no authoritative resolution in the sources.

---

## 1. ARCHITECTURAL BOUNDARIES

`FACT` Nine layers are documented, each with a stated purpose, module set, and an explicit statement of what it does not do.

| Layer | Purpose | Primary modules |
|---|---|---|
| Governance Kernel | Domain-blind validation of observations, decisions, cassette structure | `episode.py`, `event_v1.py`, `outcome_v1.py`, `cassette_interface.py`, `cassette_capabilities.py`, `cassette_schema.py`, `cassette_forensics.py`, `cassette_loader.py` |
| Data / Event | Turn raw inputs into provenance-stamped events | `event_v1.py`, `twilio_log_ingestion.py` |
| Decision | Assemble episodes, route to cassette judgment | `production_harness.py`, `sentinel_worker.py`, `api_server_resilient.py`, `claude_governance_api.py` |
| Domain Cassette | Domain-specific judgment behind a common interface | `cassettes/ivr_cassette.py`, `banking_cassette.py`, `mortgage_cassette.py` |
| Regulatory | Read-only compliance evaluation | `regulatory_cassette_interface.py`, `regulatory_checks.py`, `regulatory_deck.py`, `regulatory_cassettes/cfpb_reg_b.py` |
| Persistence | Durable tamper-evident storage | `governance/ledger_postgres.py`, `verify_chain.py`, `twin_custody.py`, `canonical_fields.py`, `ledger_immutability.sql` |
| Interface | HTTP and CLI access | `api_server_resilient.py`, `obligation_sweep.py`, `sentinel_worker.py` |
| Simulation | Offline batch execution, no governance infrastructure | `Sim/iceberg_complete_simulator.py`, `Sim/cluster_runner.py`, `Engines/simple_rl_trainer.py` |
| Infrastructure | Deployment, provisioning, CI | `.github/workflows/tests.yml`, `docker-compose.yml`, `Dockerfile`, `scripts/twin_ensure_services.sh`, `conftest.py` |

**Production path versus scaffolding.** This distinction matters more than the layer list, because the repository contains substantial code that is documented as not participating in production.

`FACT` Marked simulator-only or non-production by their own module READMEs, added July 23: `Engines/` ("Simulator-only, no production"), `Domain/` ("Generic structures, no domain assumptions"), `Model/` ("Graph building — mutable, no versioning"), `Sim/` ("Batch processing, no async"), `observe/` ("Physiological early-warning").

`FACT` `Sim/iceberg_complete_simulator.py` is annotated "[No governance/ledger — in-memory only]".

`DERIVED` Two exceptions cross the line: `Engines/simple_rl_trainer.py` and `Engines/bayes_learning_loop.py` are imported by `ivr_cassette.py`, and `bayes_learning_loop.py` is imported by `production_harness.py`. So the `Engines/` directory is labeled simulator-only while two of its modules are reachable from the live path.

`INTERPRETATION` Treat the "simulator-only" labels as statements about intent, not as guarantees of isolation. Verify reachability before assuming a module is inert.

---

## 2. FOUNDATIONAL MODULES

`FACT` The eight modules of the Core Governance Kernel, with documented roles:

| Module | Role | Notes recorded in the sources |
|---|---|---|
| `episode.py` | Frozen observation record and its validation | Enforces reason-on-any-outcome-mismatch; validates `actor_discrepancies` |
| `cassette_interface.py` | Kernel cassette abstract base class — `judge`, `explain`, `manifest` | Rewritten July 23 as kernel-only; capabilities moved out |
| `cassette_capabilities.py` | Capability registration and parameter ownership | Anti-placeholder rule; `require_capabilities` gates; pure registry with no imports of its own except an ABC subclass check |
| `cassette_forensics.py` | Code-hash computation for tamper detection | Defines the `_GOVERNANCE_CODE_MODULES` surface |
| `cassette_schema.py` | Cassette manifest validation | Version 2.0+ format, manifest-first validation |
| `event_v1.py` | Stamped observation records | VERIFIED / ATTESTED / ESTIMATED; added July 29 |
| `outcome_v1.py` | Outcome tracking and maturation rules | Added July 29; carries The Provenance Rule in its module docstring |
| `cassette_loader.py` | Dynamic cassette loading and `sys.modules` registration | Fixed July 24 to use real `importlib` with a dynamic-loading fallback |

`FACT` Four modules are flagged as large, at 500+ lines: `production_harness.py`, `governance/ledger_postgres.py`, `cassette_forensics.py`, `regulatory_checks.py`.

`FACT` Recently changed as of July 29–30, and therefore the least settled: `production_harness.py` (refactored for event integration), `obligation_sweep.py` (new), `obligation_supersession.py` (new), `twilio_log_ingestion.py` (refactored for generic events).

---

## 3. DEPENDENCY RELATIONSHIPS

`FACT` Three rules govern import direction:

1. Cassettes import interfaces; interfaces never import cassettes. The abstract base class lives in the kernel.
2. Governance imports schema and cryptography only — never business logic.
3. Tests may import anything.

`FACT` No circular imports are detected in the production path, confirmed by the suite importing all modules.

`FACT` The kernel's internal shape:

```
episode.py            → outcome_v1.py, cassette_schema.py    [no cassette imports]
event_v1.py           → outcome_v1.py
outcome_v1.py         → canonical_fields.py
cassette_interface.py → cassette_schema.py, cassette_forensics.py
cassette_capabilities.py → (registry only; ABC subclass check against cassette_interface)
cassette_forensics.py → governance/canonical_fields.py
cassette_loader.py    → cassette_interface.py, sys.modules
```

`FACT` `production_harness.py` is the widest dependency in the system, importing `episode.py`, `cassette_interface.py`, `cassette_loader.py`, `governance/ledger_postgres.py`, `regulatory_deck.py` (optional), `twilio_log_ingestion.py`, `Engines/bayes_learning_loop.py`, and `rate_limiter_v2.py`.

`DERIVED` `production_harness.py` is consequently the highest-risk file in the repository. Both service entry points route through it, and it is the single point where the kernel, the cassette layer, the ledger, the regulatory layer, the event layer, the learning loop, and rate limiting all meet. It is also on the recently-refactored list.

`FACT` `canonical_fields.py` is depended on by `outcome_v1.py`, `cassette_forensics.py`, `ledger_postgres.py`, `verify_chain.py`, and `regulatory_cassette_interface.py`, and holds the hashing specification including `OPTIONAL_HASHED_FIELDS` for maturation rules.

`DERIVED` Because it defines how records are hashed and five modules depend on it, a change to `canonical_fields.py` can invalidate previously written chain hashes across the whole ledger. It is the most consequential small file in the repository.

---

## 4. ISOLATION BOUNDARIES

`FACT` **Kernel / domain.** The kernel imports no cassette code. `episode.py` carries the annotation "[No cassette imports — kernel is domain-blind]".

`FACT` **Capability gating.** A cassette declaring a parameter owned by a capability it has not enabled is refused at load time, not at run time. This is the anti-placeholder rule.

`FACT` **Regulatory / decision.** Lenses are read-only with respect to the decision. `judge()` makes zero outside calls; `explain()` reports only. Findings are recorded as disclosures.

`FACT` **Disclosure ordering.** Compliance records are written before the corresponding action is taken. Stated as "disclosure precedes effect" and "compliance logging must succeed first."

`FACT` **Simulation / production.** The simulator uses an in-memory ledger and touches no governance storage.

`FACT` **Custody separation.** The twin runs under its own PostgreSQL role (`twincustodian`), distinct from the primary runtime identity (`ledger_reader`), with a sealed envelope over the transport.

`FACT` **Verdict / behavior.** The kernel verdict does not drive behavior. `quality_score` still controls routing; the verdict is recorded alongside it. Described in the sources as "additive, not a replacement."

---

## 5. STATE LOCATIONS

| State | Where it lives | Rules recorded in the sources |
|---|---|---|
| Decision records | `ledger_entries` (PostgreSQL 16) | Append-only via BEFORE INSERT trigger; hash-chained on `current_hash` / `previous_hash`; carries `cassette_version`, `governance_decision`, `governance_params`, `outcome_obligation`, `outcome_harm_event` |
| Obligations | `obligation_ledger` — `VERIFIED` twin-side (created in `twin_receiver.py`) | `state` constrained to OPEN / RESOLVED / ABANDONED; keyed by `obligation_id`, references `decision_hash` |
| Cohort findings | `cohort_review_ledger` — `VERIFIED` twin-side (created in `twin_receiver.py`) | Holds `cohort_equity_review` JSONB from dimensions 4–6 |
| Twin replica | Separate PostgreSQL instance/role | Same schema, sealed envelope, peer-auth over Unix socket |
| Rate-limit counters | Redis 7 | Circuit-breaker pattern |
| Learning-loop belief state | Redis 7 | Fail-open — falls back to in-memory if Redis is unreachable |
| TLS material | `certs/cert.pem`, `certs/key.pem` | Generated at runtime if absent; gitignored |
| Loaded cassette modules | `sys.modules` | Registered by `cassette_loader.py` |
| Cassette manifest | In code, as class attributes | `DERIVED` Not a table; reflected into the `cassette_version` string (`domain:name:version`) bound at ledger write time |
| Episode | No dedicated table | `DERIVED` Reflected into the `governance_decision` JSONB field of `ledger_entries` at recording time |
| Raw events | — | `UNKNOWN` The sources do not state whether events are persisted independently of the episode they are folded into |

`VERIFIED` (corrected in v3) This document originally listed the location of `obligation_ledger` and `cohort_review_ledger` as unresolved between the sources. A subsequent verification pass against the live repository found both tables created in `twin_receiver.py` — they live twin-side, unambiguously. The source documents' inconsistency was theirs alone.

`DERIVED` This was the first thing worth establishing before writing any query against obligations, and it is now answered: query the twin, not the primary.

---

## 6. TRUST BOUNDARIES

`FACT` **Runtime identity.** Production runs as `ledger_reader`, holding SELECT and INSERT only. The system raises `RuntimeError` if the identity is unset, and refuses to run if that identity is the table owner or a superuser. Documented as fail-closed.

`FACT` **Role separation.** `iceberg` owns the primary ledger. `ledger_reader` is the production runtime. `sentinelsvc`, `twincustodian`, and `twincustomer` are the twin security domains.

`FACT` **Actor self-report.** Distrusted by design. Cross-checked against observed data in `episode.py`; discrepancies recorded in `actor_discrepancies`. The actor's self-report is never read by judgment.

`FACT` **Chain verification.** Recomputed independently at three sites — the writer, `verify_chain`, and `twin_custody`. A new record kind is not considered complete until all three agree.

`FACT` **Cassette binding.** `require_cassette_binding=True` is documented as fail-closed. A governance-decision hash mismatch raises `cassette_version_conflict`.

`FACT` **External services are outside the trust boundary and partially unimplemented.** Twilio's stream fetch returns an empty list. The US Census Geocoder is referenced but live calls are not confirmed. The Anthropic API is partial — explanations implemented, the GALLM executor stubbed with a hardcoded return.

`UNKNOWN` No source document describes authentication or contractual detail for any external service.

---

## 7. EXTENSION POINTS

**Adding a cassette** — `FACT` six documented steps:

1. Extend `cassette_interface.CassetteBase`.
2. Declare `CAPABILITIES` and `REQUIRED_GOVERNANCE_PARAMETERS`.
3. Implement `judge()` and `explain()`.
4. Implement `get_maturation_rule()` if enabling the outcome-obligation capability.
5. Add tests at `Tests/test_<domain>_cassette.py`.
6. Bump the version in `domain:name:version` form.

`FACT` The mortgage cassette is the documented example of a minimal cassette: it declares the outcome-obligation capability only, with no call-center surface at all.

**Adding a module** — `FACT` five documented steps: place it in the appropriate directory; update `.github/workflows/tests.yml` if it introduces a new test location; add it to `cassette_forensics._GOVERNANCE_CODE_MODULES` if it is kernel-related; bump the affected cassette version because the code hash has changed; add a README entry if it is directory-level.

`DERIVED` Step three is the one most likely to be missed and the most damaging to miss. Omitting a kernel-related module from the governance code surface means changes to it are not covered by tamper detection.

**Adding a regulatory lens** — `FACT` `regulatory_cassettes/` contains `cfpb_reg_b.py` and a placeholder for "[Other regulatory lenses as added]". Lenses subclass the lens ABC in `regulatory_cassette_interface.py` and draw dimension checkers from `regulatory_checks.py`. `UNKNOWN` No step-by-step procedure for adding a lens is documented, unlike the cassette procedure.

**Deploying a cassette** — `FACT` tag and version it, allow the harness to hash-check and refuse mismatches at deployment time, then confirm binding in the ledger by selecting distinct `cassette_version` values for that domain prefix.

**Configuration as an extension surface** — `FACT` Dimensions 2 and 3 are enabled per lens profile via `enable_input_authorization_tier_screen` and `enable_narrative_legitimacy_screen`. Dimensions 1, 4, and 5 are always on. In the reference lens, all parameters are configuration data rather than code.

---

## 8. INVARIANTS

`FACT` Ten decisions are recorded as locked, with the instruction "Do Not Litigate," each traced to an enforcement point:

| Invariant | Enforced at |
|---|---|
| Ledger is append-only | `governance/ledger_immutability.sql`, BEFORE INSERT trigger |
| Twin is an independent witness | `governance/twin_custody.py`, sealed envelope + separate role |
| Actor self-report is distrusted | `episode.py:validate_episode()` |
| A reason is required on any outcome mismatch | `episode.py` `actor_discrepancies` logic; validation fails without it |
| The judge verdict does not drive behavior | `production_harness.py:process_call()` — verdict rides alongside `quality_score` |
| No implicit cassette parameters | `cassette_capabilities.py` anti-placeholder rule, fail-closed at load |
| Regulatory lenses are read-only | `regulatory_deck.py:judge()` — zero external calls |
| Disclosure precedes effect | `regulatory_deck.py:judge()` writes `regulatory_cassette_inserted` first |
| Two records, two lifespans — the decision is permanent, the obligation is durable | `episode.py`, `outcome_v1.py`, `obligation_ledger` |
| The Provenance Rule — every claim stamped VERIFIED, ATTESTED, or ESTIMATED, never interchangeable; if unknown, timestamp why and what would close it | `outcome_v1.py` module docstring |

`INTERPRETATION` Three of these will read as bugs to an engineer encountering them cold: a verdict that changes nothing, a load-time refusal over an unused parameter, and a hash mismatch whose remedy is to rebuild a table from a replica. They are deliberate. Treat an urge to simplify any of the ten as a signal to ask rather than to patch.

---

## 9. ASSUMPTIONS AND UNRESOLVED STRUCTURE

**Environment assumptions**
`FACT` Python 3.9+. PostgreSQL 16 and Redis 7 pinned explicitly in CI and compose. `httpx` pinned below 0.28 due to an incompatibility with Anthropic SDK 0.116.0. `ruff` pinned at 0.15.22 to prevent local/CI drift. CI uses the runner's system Python, with the PEP 668 interaction recorded as unverified.

**Testing assumptions**
`VERIFIED` (corrected in v3) This document originally stated that the CI-equivalent command excludes `test_twin_live.py`, and that its 18 tests can only be run locally. That description was accurate for an earlier state of the repository but is stale as of commit `d881bc0`, which switched the CI job itself to a natively installed PostgreSQL and added a step provisioning the OS identities `test_twin_live.py` needs — the full suite, twin tests included, now runs in CI with no exclusion flag. `scripts/twin_ensure_services.sh` remains the correct step for running that suite in a local environment outside CI. One test is known-flaky under load — a wall-clock latency assertion that passes in isolation. Suites using persistent external services fail occasionally when the PostgreSQL or Redis daemon is reaped mid-container; a restart before the suite is the documented remedy.
`FACT` No static type checking is enforced. There is no mypy or pydantic gate in CI.
`FACT` The bandit result of zero medium-and-above findings was reached by annotating and justifying one high and seventeen medium findings, not by their absence.

**Documented limitations that are out of scope, not bugs**
`FACT` Node-naming coupling — queue and agent detection by substring matching. `FACT` `Sim/cluster_runner.py`'s `run_batch()` does not guarantee stable ordering. `FACT` `Model/` graph building is mutable with no versioning. `FACT` The PostgreSQL constructor lock optimization is a known issue left unfixed by decision. `FACT` `docker-compose.yml`'s `depends_on` waits only for container start, not database readiness; a `pg_isready` healthcheck is the documented mitigation.

**Disclosed design-level gaps**
`FACT` Renaming a variable defeats the proxy and tier screens — classified as the same class of problem as latent proxies, requiring a new approach. `VERIFIED` (corrected in v3) This document originally stated that free-text narrative is never captured at decision time. That is false: a `reason` column is populated on every decision, via `append_decision()` writing the deciding component's `reasoning` field. In production, that value is the AI's own self-reported reasoning — the same self-report `episode.py` is designed to distrust elsewhere — not an independently captured business justification. The gap that survives correction is narrower than originally stated: no independent business-reason field exists, not that nothing is captured.

**Structural questions the sources raise about themselves and do not answer**

`VERIFIED` (corrected in v3) This document originally listed which copy of `cassette_forensics.py` is live as unresolved, following the source directory map's "(duplicate entry point? verify which is live)" annotation. Direct inspection of the repository found exactly one `cassette_forensics.py`. The apparent duplication was an artifact of the source documentation, not a real condition in the codebase. This item is withdrawn.
`UNKNOWN` Whether root-level `cassettes/` and `regulatory_cassettes/` are symlinks or duplicates of the package-level directories — annotated "(symlink or duplicate?)".
`UNKNOWN` Where `bisg_estimator.py` resides. Two modules import it; it appears in no directory listing or module table.
`UNKNOWN` Whether the mortgage cassette is in CI. Its code-hash version is recorded as "N/A (not in CI yet)" while its 27 tests appear inside the suite total.
`UNKNOWN` How many `conftest.py` files exist and what each owns — one is claimed at root and one in `Tests/`, but a third is shown at `sentinel_os/conftest.py`.
`UNKNOWN` What triggers the obligation sweep. The CLI is operator-invoked and no scheduling mechanism is described.
`UNKNOWN` Whether the compliance rollup spans six dimensions. It is glossed as AND-logic across six, while three of the six are documented as unwired.

---

## 10. THINGS AN ENGINEER MUST UNDERSTAND BEFORE MODIFYING THE SYSTEM

1. `FACT` **Editing kernel code changes hashes that stored records are bound to.** Code-hash movement is the documented trigger for a cassette version bump, and binding enforcement refuses mismatches. Plan the version bump as part of the change, not after it.
2. `FACT` **You cannot rewrite ledger rows.** The trigger forbids it and the runtime role lacks the permission. `UNKNOWN` No migration procedure for `ledger_entries` is documented; the only recorded recovery from a hash conflict is to drop the table and rebuild from the twin.
3. `FACT` **A new record kind touches three recompute sites.** Writer, `verify_chain`, and `twin_custody` must independently agree, and the record is not complete until they do.
4. `FACT` **Do not let the kernel import a cassette.** Domain-blindness is the property the whole cassette model rests on.
5. `FACT` **Do not give a lens write access during `judge()`.** Zero external calls is the documented contract.
6. `FACT` **Do not reorder disclosure and effect.** Compliance logging must succeed first.
7. `FACT` **Do not wire the kernel verdict into behavior.** It is additive by decision. Changing that is a governance decision, not a refactor.
8. `FACT` **Declare a capability before adding its parameters.** The anti-placeholder rule fails the load, and it fails at load time, so a mistake here surfaces as a startup failure rather than a test failure.
9. `FACT` **Register kernel-related modules in `_GOVERNANCE_CODE_MODULES`.** Omission silently removes them from tamper detection.
10. `FACT` **A change to `canonical_fields.py` reaches everything hashed.** Five modules depend on it, including the ledger writer and the verifier.
11. `FACT` **`production_harness.py` is the convergence point.** Eight subsystems meet there, both service entry points run through it, and it was refactored within the last two days of the documented baseline.
12. `FACT` **The pre-submission gate is four checks:** the full suite at roughly 670 passed / 6 skipped, `ruff check .` at zero findings, `bandit -r . -ll` at zero findings, and the cassette forensics test confirming hash-mismatch detection still works. Twin verification is a fifth, optional, local-only check.

---

## 11. QUESTIONS A NEW ENGINEER SHOULD ASK BEFORE CONTRIBUTING

**Answerable only by the maintainer — the sources do not contain these**

1. ~~Which copy of `cassette_forensics.py` is live?~~ **Resolved by verification** — only one file exists; no question remains.
2. ~~Are the root-level `cassettes/` and `regulatory_cassettes/` directories symlinks, duplicates, or stale?~~ **Resolved by verification** — `api_server.py` and `certs/` are confirmed genuine symlinks; not stale, not duplicates.
3. ~~Where is `bisg_estimator.py`?~~ **Resolved by verification** — it exists at `sentinel_os/`; the source maps simply omitted it. Whether it is covered by tamper detection was not separately re-verified and remains open.
4. Is the mortgage cassette running in CI, and what is its actual bound version?
5. ~~Do `obligation_ledger` and `cohort_review_ledger` live on the primary, the twin, or both?~~ **Resolved by verification** — both are created in `twin_receiver.py` and live twin-side, unambiguously.
6. What is the intended migration path for `ledger_entries` when a schema change is unavoidable?
7. What is meant to trigger the obligation sweep in a running deployment?
8. Which of the six compliance dimensions are intended to be wired, and which are deliberately left off?
9. Who has authority to change one of the ten locked decisions, and what does that process look like?
10. `UNKNOWN` What is the branch, review, and merge protocol? The sources reference patches applied by one individual and a pre-submission checklist, but no code-review process is documented.

**Answerable from the repository, and worth doing before the first change**

11. Trace one decision end to end through all eight documented runtime steps, from ingestion to cohort review.
12. Run the suite twice back to back, as the sources report doing, and confirm the same result.
13. Provision the twin locally and run the 18 excluded tests, since CI never exercises them.
14. Deliberately break a cassette hash and confirm the refusal behaves as documented.
15. Confirm for yourself which `Engines/` modules are reachable from `production_harness.py`, given that the directory is labeled simulator-only while two of its modules are imported by live code.

---

## 12. WHAT TO READ, IN ORDER

`INTERPRETATION` A reading order implied by the dependency structure rather than stated in the sources:

1. `episode.py` — the record everything else is about, and the validation rules.
2. `outcome_v1.py` — including the module docstring, which carries the governing provenance rule.
3. `event_v1.py` — how a fact acquires a confidence label.
4. `cassette_interface.py` and `cassette_capabilities.py` — the extension contract and its enforcement.
5. `governance/canonical_fields.py` — how anything becomes a hash.
6. `governance/ledger_postgres.py` and `ledger_immutability.sql` — how a record becomes permanent.
7. `governance/verify_chain.py` and `twin_custody.py` — how tampering is detected.
8. `cassettes/mortgage_cassette.py` — the smallest complete cassette.
9. `production_harness.py` — last, because it only makes sense once the other eight do.

---

**End of Document 2.**

`FACT` Grounded solely in the four source documents at repository state `68cadfb`. Where a procedure a contributing engineer would normally expect is absent from those documents, it is marked `UNKNOWN` rather than reconstructed.
