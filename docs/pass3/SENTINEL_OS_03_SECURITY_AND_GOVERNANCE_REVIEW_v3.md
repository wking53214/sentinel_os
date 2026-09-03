> **⚠ Point-in-time snapshot — not maintained.** Documented baseline `68cadfb` (July 29, 2026). Much has landed since — PRs #28–#45 as of 2026-09-03: the keyed `authorized_by` ledger attestation with key rotation, the persisted observed-event layer, the **extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo** (2026-08-28) — its standalone simulator, `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree, Twilio ingestion, Claude governor client, and queue/staffing/Bayes layer are no longer in this repo — the mandatory `conservation/` boundary, and a widened CI gate set. Treat every directory map, module inventory, test count, and CI description below as historical. For current state see the [repository root README](../../README.md) and **[POST_SNAPSHOT_CORRECTIONS_2026-09-03.md](./POST_SNAPSHOT_CORRECTIONS_2026-09-03.md)**, which reconciles the specific quantitative claims (test totals, the security-scan framing, the mortgage-cassette-in-CI question) a present-day reader would otherwise take as current.

---

# DOCUMENT 3 — SECURITY AND GOVERNANCE REVIEW

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification:** `FACT` = stated in a source document · `DERIVED` = follows from two or more documented facts · `INTERPRETATION` = reasonable reading, not established · `UNKNOWN` = not in the sources.

**Scope note.** This document records the control set as documented. It does not evaluate control adequacy and does not recommend changes. Where a control a reviewer would look for is absent from the source documents, that absence is recorded as an observation about the documented control set — not as a finding against the system, and not as a recommendation.

**Correction notice (v3).** A subsequent verification pass against the live repository at commit `68cadfb` found that commit `d881bc0` had already moved the twin-replica tests into continuous integration, contrary to what the four source documents stated. Every claim below that the twin's 18 tests are excluded from CI has been corrected and is marked `VERIFIED`.

---

## 0. READER FRAME

**AUDIENCE**
Security engineer, governance architect, third-line risk reviewer.

**READER QUESTIONS**
1. What is the trust model, stated explicitly rather than implied?
2. Which controls are enforced below the application layer, where an application compromise cannot reach them?
3. What evidence does the system produce, where is it produced, and can it be checked independently?
4. Who holds which authority, and what is each identity prevented from doing?
5. Which controls are verified by automation, and which are asserted?
6. What is inside the trust boundary that a reviewer would expect to be outside it?

**DECISION OBJECTIVE**
Determine whether the documented control set is coherent enough to warrant a hands-on assessment, and identify which controls a hands-on assessment must test directly because the documentation cannot establish them.

**TRUST FAILURE**
This reader loses confidence if the following are not stated up front:

- `VERIFIED` (corrected in v3) The 18 tests that exercise the twin replica — the mechanism the entire tamper-detection claim rests on — now run in continuous integration on every commit, as of `d881bc0`. This document's original claim that they were excluded is stale.
- `FACT` No authentication or authorization mechanism for the HTTP API is described anywhere in the four sources, though the API exposes judge, explain, and ledger-query endpoints.
- `FACT` The zero-finding security-scan result was achieved by annotating and justifying one high-severity and seventeen medium-severity findings, not by their absence.
- `FACT` Three of six compliance dimensions are active in the default configuration.
- `FACT` Credentials appear inline in documented command examples, and the development and CI database credentials are `iceberg` / `iceberg`.

---

## 1. WHAT SENTINEL TRUSTS

`DERIVED` The trust model is unusually explicit for a system of this size, but the trusted set is broader than the design narrative suggests. Each item below is something the system's guarantees depend on and does not itself verify.

| Trusted element | Why the system depends on it | Source basis |
|---|---|---|
| **PostgreSQL 16 as an enforcement engine** | Immutability is a BEFORE INSERT trigger. If the database engine or its configuration is compromised, the primary immutability control is compromised with it | `FACT` `ledger_immutability.sql` |
| **The `iceberg` owner role** | Owns `ledger_entries`. The runtime role is deliberately constrained; the owner is not documented as constrained | `FACT` role list; `UNKNOWN` owner restrictions |
| **The cryptographic implementation** | Twin custody rests on X25519 + AES-GCM via the `cryptography` package | `FACT` dependency list |
| **The governance code surface definition** | `_GOVERNANCE_CODE_MODULES` in `cassette_forensics.py` defines what tamper detection covers. Anything not registered there is outside the hashed surface | `FACT` module table and the documented step "add to `_GOVERNANCE_CODE_MODULES` if kernel-related" |
| **The cassette's own capability declaration** | `CAPABILITIES` is asserted by the cassette. The anti-placeholder rule checks the declaration for internal consistency — that no parameter belongs to a disabled capability — not whether the declaration is truthful about what the code does | `FACT` capability registry; `DERIVED` scope of the check |
| **Operator-supplied environment configuration** | Ledger DSN, Redis URL, twin receiver URL, replica ID, and the twin ship token all arrive as environment variables | `FACT` configuration section |
| **The self-signed TLS certificate** | Generated at runtime if absent. No external trust anchor is documented | `FACT` `certs/cert.pem` described as self-signed |
| **The US Census Geocoder** | Supplies BISG demographic estimates and ZIP/county extraction that feed compliance conclusions | `FACT` external integration; `FACT` live-call status not confirmed |
| **Agreement among three recompute sites** | Chain integrity is trusted when the writer, verifier, and twin independently agree | `FACT` three recompute sites |
| **Redis availability, weakly** | Rate limiting and belief state use Redis and are documented fail-open, falling back to in-memory | `FACT` fail-open behavior |

`DERIVED` The strongest structural property of the trust model is that immutability and custody separation are enforced outside the application process — one in the database engine, one under a different operating-system and database identity. An application-level compromise does not by itself grant the ability to rewrite history or to forge the second copy.

`FACT` The twin's role separation is reinforced at the operating-system level: `scripts/twin_ensure_services.sh` provisions OS identities as well as PostgreSQL roles, and the twin's verification path uses Unix-socket peer authentication.

---

## 2. WHAT SENTINEL DISTRUSTS

`FACT` Distrust is implemented as named, code-enforced mechanisms rather than as policy language.

| Distrusted element | Mechanism | Enforcement point |
|---|---|---|
| The acting system's account of itself | Self-report cross-checked against observed data; discrepancies recorded; the self-report is never read by judgment | `episode.py:validate_episode()`, `actor_discrepancies` |
| Unexplained outcome mismatches | A written reason is mandatory on any mismatch; validation fails without it | `episode.py` |
| Its own runtime privileges | Runtime identity limited to SELECT and INSERT; startup refused if the identity is unset, or is the table owner or a superuser | `ICEBERG_LEDGER_DSN` / `ledger_reader` fail-closed check |
| Its own stored copy of history | An independently held twin exists specifically so the primary is not self-attesting | `governance/twin_custody.py` |
| The integrity of decision logic over time | Code hash bound at write; mismatch raises `cassette_version_conflict` | `cassette_forensics.py`, `require_cassette_binding=True` |
| Undeclared configuration | A parameter belonging to a disabled capability is refused at load time | `cassette_capabilities.py` anti-placeholder rule |
| Unqualified claims | Every observation stamped VERIFIED, ATTESTED, or ESTIMATED, and the stamps are not interchangeable | `event_v1.py` |
| Vague unresolved states | An unknown must record why it is unknown and what would close it | `outcome_v1.py` — The Provenance Rule |
| Inputs not on an approved list | Seven-tier input-authorization ladder, T0 through T6, including undeclared and vendor-opaque tiers | Dimension 2, `regulatory_checks.py` |
| Self-declared tier claims | A tier confidence scale accompanies the dimension framework | `FACT` regulatory_cassettes README: "Five-dimension framework + tier confidence scale" |
| The stated reason for a decision | Narrative legitimacy screening of free text against declared policy | Dimension 3 |
| Renamed variables | Correlation-based proxy detection over numeric and boolean cohort data | Dimension 5 |
| Its own verdict's authority | The kernel verdict is recorded but does not drive behavior | `production_harness.py:process_call()` |

`INTERPRETATION` The last row is the one a reviewer is least likely to expect and most likely to find significant. A governance component that refuses to act cannot be the proximate cause of an operational harm, which materially changes its risk profile relative to a system that gates decisions.

---

## 3. WHERE EVIDENCE IS GENERATED

`FACT` Nine documented generation points, with the artifact each produces:

| # | Generation point | Artifact produced | Storage |
|---|---|---|---|
| 1 | Event stamping — `event_v1.py`, `twilio_log_ingestion.py` | Observation with a VERIFIED / ATTESTED / ESTIMATED stamp | `UNKNOWN` whether persisted independently of the episode |
| 2 | Episode validation — `episode.py` | Frozen observation record; `actor_discrepancies`; mandatory mismatch reason | `DERIVED` reflected into `governance_decision` JSONB; no dedicated table |
| 3 | Ledger append — `governance/ledger_postgres.py` | Hash-chained row carrying `current_hash`, `previous_hash`, `cassette_version`, `governance_decision`, `governance_params`, `outcome_obligation`, `outcome_harm_event`, `created_at` | `ledger_entries` |
| 4 | Twin derivation — triggered by `append_decision` | Sealed-envelope replica record under separate custody | Twin PostgreSQL instance |
| 5 | Regulatory disclosure — `regulatory_deck.py` | `regulatory_cassette_inserted` event written before any effect | `DERIVED` ledger-side event |
| 6 | Regulatory findings — `regulatory_checks.py` via the lens | Per-dimension PASS / FLAG / INDETERMINATE | `DERIVED` disclosure events plus cohort records |
| 7 | Obligation lifecycle — `outcome_v1.py` | OPEN → RESOLVED or ABANDONED, with `expected_by` and a declared maturation rule | `obligation_ledger` |
| 8 | Cohort sweep — `obligation_sweep.py` | `cohort_equity_review` over dimensions 4–6, posted to the twin for recording | `cohort_review_ledger` |
| 9 | Chain verification — `verify_chain.py` | Clean/dirty result plus findings, on demand | Returned to caller; `UNKNOWN` whether the verification result is itself recorded |

`FACT` A tenth category exists but concerns the code rather than the decisions: CI produces test, lint, and security-scan results — roughly 670 passed / 6 skipped, zero ruff findings, zero bandit findings at medium and above, first fully green July 24, last five recorded runs green.

`DERIVED` Item 9 is the reviewer-facing control and it is on-demand rather than continuous. No source describes scheduled or automatic chain verification, and no source describes whether a verification event is itself written to the ledger — meaning the documentation does not establish that a verification ever occurred at a given time.

---

## 4. HOW PROVENANCE IS PRESERVED

`FACT` **Three-value stamping.** Every observation carries VERIFIED, ATTESTED, or ESTIMATED. The governing rule, recorded in the `outcome_v1.py` module docstring: "Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."

`FACT` **A graded scale for authorization claims.** Dimension 2 implements a seven-tier ladder, T0 through T6, spanning prohibited, regulator-filed, permitted, internal-only, pending, undeclared, and vendor-opaque inputs. A tier confidence scale accompanies it.

`FACT` **INDETERMINATE is a first-class result.** Dimensions return PASS, FLAG, or INDETERMINATE. Where free-text narrative is unavailable, the affected dimension reports INDETERMINATE rather than passing by default.

`FACT` **Declared resolution conditions are hashed.** Maturation rules — for example, loan performance at three years — are declarations recorded against the obligation, with `OPTIONAL_HASHED_FIELDS` in `canonical_fields.py` supporting their hashing.

`FACT` **Decision logic identity is bound to the record.** Each ledger row carries `cassette_version` in `domain:name:version` form, and a code-hash change is the documented trigger for a version bump.

`DERIVED` Together these mean a reader of a stored decision can, in principle, distinguish a measured fact from an estimate, identify which version of which domain logic produced the decision, see what was not known and why, and see what condition would resolve it. `INTERPRETATION` This is the system's most distinctive property. It is also the property most dependent on discipline rather than enforcement: nothing documented prevents a future contributor from stamping an estimate as verified.

---

## 5. HOW RECORDS ARE PROTECTED

| Control | Enforcement location | Layer |
|---|---|---|
| Append-only writes | BEFORE INSERT trigger in `ledger_immutability.sql` | Database engine |
| No UPDATE or DELETE available to the running system | `ledger_reader` role holds SELECT and INSERT only | Database privileges |
| Refusal to run over-privileged | Startup check rejects an unset identity, the table owner, or a superuser | Application, fail-closed |
| Sequential integrity | `current_hash` / `previous_hash` chaining, both columns UNIQUE | Data model |
| Independent second copy | Twin under separate PostgreSQL role and separate OS identity | Custody separation |
| Confidentiality in transit to the twin | Sealed envelope, X25519 + AES-GCM | Cryptographic |
| Twin authentication | Unix-socket peer authentication | Operating system |
| Transport certificate | Self-signed, generated at runtime if absent | `FACT` no external trust anchor documented |
| Secret hygiene in the repository | `.gitignore` covers `certs/*.pem`, `*.key`, `*.crt`, plus `*.patch`, `*.bundle`, `*.diff` | Source control |

`FACT` The documented remedy for a governance-decision hash mismatch is to drop `ledger_entries` and rebuild from the twin — characterized in the sources as binding enforcement working correctly.

`DERIVED` That remedy makes the twin load-bearing for recovery as well as for detection. The twin is therefore not a redundant copy in the availability sense; it is the only documented path back from a detected conflict.

**Observations about the documented control set**

`UNKNOWN` No encryption at rest is described for either ledger. `UNKNOWN` No key management, key rotation, or secret-store integration is described; the twin ship token is documented as an environment variable holding sealed-envelope key material. `UNKNOWN` No backup or retention policy is described, for a record type whose obligations carry multi-year horizons. `UNKNOWN` No access logging, audit logging of reads, or monitoring and alerting is described. `FACT` Documented command examples embed credentials inline, and development and CI credentials are `iceberg` / `iceberg`.

---

## 6. HOW TAMPERING IS DETECTED

`FACT` Four mechanisms:

1. **Chain reconstruction.** `verify_chain.py` rebuilds and verifies the hash chain and returns findings.
2. **Three-site agreement.** The chain is recomputed independently at the writer, at `verify_chain`, and at `twin_custody`. A new record kind is not treated as complete until all three agree.
3. **Independent custody comparison.** The twin holds a sealed copy under a different identity, for comparison against the primary.
4. **Code-hash binding.** A mismatch between recorded and running decision logic raises `cassette_version_conflict`.

`FACT` Detection capability is itself tested: the pre-submission checklist includes running the cassette forensics test with the stated expectation that hash-mismatch detection works.

**Verification status of each control**

Legend: 🟢 exercised by automated CI · 🟡 exercised only in a local environment · ⚪ no verification described in the sources

| Control | Status | Basis |
|---|---|---|
| Append-only trigger | 🟢 | `FACT` `Tests/conftest.py` reapplies `ledger_immutability.sql` per test; ~80 governance tests |
| Hash chaining and chain verification | 🟢 | `FACT` `test_governance_verification.py` listed among the complex test files |
| Cassette hash binding | 🟢 | `FACT` `Tests/test_cassette_forensics.py` in the pre-submission gate |
| Capability and anti-placeholder gating | 🟢 | `FACT` ~40 cassette loading and validation tests |
| Regulatory dimensions 1, 4, 5 | 🟢 | `FACT` ~100 regulatory tests |
| Event and outcome stamping | 🟢 | `FACT` 102 EventV1/OutcomeV1 tests |
| Twin custody, sealed envelope, peer authentication | 🟢 | `VERIFIED` (corrected in v3) 18 tests in `test_twin_live.py`, run in CI as of `d881bc0`, which added native PostgreSQL and OS-identity provisioning to the CI job specifically for this purpose |
| Regulatory dimensions 2, 3 | 🟡 | `FACT` Implemented and opt-in; off unless configured |
| Regulatory dimension 6 | ⚪ | `FACT` Implemented, not wired to the live path; patch prepared and unapplied |
| Per-decision compliance evaluation | ⚪ | `FACT` Retrieval function exists, not called from the live judge path |
| Adversarial tamper attempt | ⚪ | `UNKNOWN` No source describes testing against a deliberate adversary |
| API authentication and authorization | ⚪ | `UNKNOWN` No mechanism described |

`VERIFIED` (corrected in v3) This document originally concluded that the twin — the control providing independence — was verified only locally and never in CI, and treated that as the single most consequential observation in the review. That conclusion no longer holds: `d881bc0` moved the twin's 18 tests into CI, so the independence control is now exercised continuously alongside the others. The remaining concentration worth noting is narrower: regulatory dimensions 2, 3, and 6, per-decision compliance evaluation, adversarial testing, and API authentication remain unverified or undocumented. Those are gaps in coverage of specific compliance and access-control features, not in the tamper-detection mechanism itself.

---

## 7. AUTHORITY BOUNDARIES

| Identity or component | Documented authority | Documented restriction |
|---|---|---|
| `ledger_reader` (production runtime) | SELECT, INSERT on the ledger | No UPDATE, no DELETE; may not be the table owner or a superuser; system refuses to start if unset |
| `iceberg` (owner) | Owns `ledger_entries`; used by development and CI | `UNKNOWN` No restriction on the owner role is documented |
| `twincustodian`, `twincustomer`, `sentinelsvc` | Twin security domains, provisioned as OS identities and database roles | `FACT` Distinct from the primary runtime identity |
| Governance kernel | Validate episodes, enforce structure, compute hashes | Imports no cassette code; has no domain knowledge |
| Cassette | Judge and explain within declared capabilities | Cannot use a parameter owned by an undeclared capability; refused at load |
| Regulatory lens | Read decision material via adapters; raise findings; write disclosures | `judge()` makes zero outside calls; `explain()` reports only; cannot alter the decision |
| Kernel verdict | Recorded to the ledger | Does not drive behavior; `quality_score` retains routing control |
| The acting system | Submits self-report | Report is cross-checked and never read by judgment |
| Human operator (one named individual) | Applies patches, sets cassette scope, makes governance calls, configures environment, invokes the sweep CLI | `UNKNOWN` Whether any other operator role exists; `UNKNOWN` no separation-of-duties, approval workflow, or code-review process described |

`DERIVED` Authority separation is implemented thoroughly between software components and between database identities, and is not documented at all between people. The same individual is recorded as author, reviewer, applier of patches, and governance decision-maker.

---

## 8. WHAT ACTIONS ARE RESTRICTED

`FACT` Restrictions that produce a hard failure rather than a warning:

| Restricted action | Failure mode |
|---|---|
| Updating or deleting a ledger row | Blocked by database trigger; privilege not held by the runtime role |
| Starting without a configured runtime identity | `RuntimeError: ICEBERG_LEDGER_RUNTIME_USER not set` |
| Running as table owner or superuser | Startup refused |
| Loading a cassette declaring a parameter of a disabled capability | Refused at load time |
| Running decision logic whose hash does not match the binding | `cassette_version_conflict` |
| Writing to `ledger_entries` without the required role grants | `permission denied for ledger_entries` |
| A lens making an outside call during `judge()` | Contractually zero; documented as read-only |
| Taking effect before disclosure is written | Ordering enforced — compliance logging must succeed first |
| Letting the kernel verdict control routing | Deliberately not permitted |

`DERIVED` Four of these fail at load or startup rather than at run time. A misconfiguration therefore surfaces as a refusal to operate rather than as a silent degradation — with one documented exception: Redis is fail-open, so loss of the rate-limiting and belief-state store degrades to in-memory behavior rather than halting.

---

## 9. WHAT A HANDS-ON ASSESSMENT WOULD HAVE TO ESTABLISH

Recorded because the source documents cannot answer these, not as recommendations.

`UNKNOWN` Whether any authentication or authorization protects the judge, explain, and ledger-query endpoints.
`UNKNOWN` Whether the twin's custody separation holds against an operator who holds both sets of credentials.
`UNKNOWN` Whether the sealed envelope's key material, delivered by environment variable, is managed by anything.
`UNKNOWN` Whether chain verification is ever run in a deployed environment, on what schedule, and whether the result is recorded.
`UNKNOWN` Whether the ledger is encrypted at rest, backed up, or subject to a retention policy commensurate with multi-year obligation horizons.
`UNKNOWN` Whether the one high and seventeen medium security findings that were annotated and justified are individually defensible.
`UNKNOWN` Whether the BISG demographic estimation path makes live external calls, and what happens to compliance conclusions when that service is unavailable or wrong.
`VERIFIED` (corrected in v3) This document originally listed as unresolved which of two copies of `cassette_forensics.py` is live, following an unresolved note in the source directory map. Direct inspection of the repository found exactly one `cassette_forensics.py`. The apparent duplication was a documentation artifact in the source maps, not a real condition in the codebase — this item is withdrawn.
`UNKNOWN` Whether any control has been tested against a deliberate adversary rather than a test fixture.
`FACT` One limitation is disclosed by the sources as an open design-level gap: renaming a variable defeats the proxy and tier screens. `VERIFIED` (corrected in v3) This document's original second item — that free-text reasoning is never captured — is false. A `reason` column is populated on every decision row with the deciding AI's self-reported reasoning. See Document 4 §3 for the full correction; the gap that remains is narrower than "never captured."

---

**End of Document 3.**

`FACT` Grounded solely in the four source documents at repository state `68cadfb`. Control adequacy is not assessed and no changes are recommended; absences are recorded as properties of the documented control set.
