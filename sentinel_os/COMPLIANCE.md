# Sentinel OS: Regulatory Compliance Manifest

**Last Updated:** August 7, 2026 (refreshed against `main` @ `ad14de2`; previous revision, July 16, had drifted ~95 commits stale — see "Since the July 16 revision" below)
**Status:** Production-Deployed — Phase 2 complete, and every item Phase 2 had left open in its own "Remaining" list has since closed at the code level (cassette-binding enforcement, forged-snapshot detection — see Known Limitations). Since then, a substantial bias/fair-lending screening framework and a data-use/retention governance layer have been built and thoroughly tested, but **neither is wired into the live decision or deployment path yet** — see "Built but not yet invoked" below. External witnessing (DAP v1 customer-held-key twin) is live and tested, with the same two caveats as before: (a) shipping is off unless `TWIN_TARGETS_FILE` is configured, and (b) regulatory acceptance of customer-held-key witnessing as a formal control has not been granted.
**Enforcement Dates:** EU AI Act (Aug 2, 2026) | NAIC Bulletin (effective 2026)
**Legend:** ✅ verified, holds as stated · ⚠️ true with a material qualification below · 🔲 architecturally supported, not shipped

## Since the July 16 revision

This document was not updated for ~3 weeks of active work. Before writing anything below, every claim in the previous revision was re-verified against the current code (not just commit messages) — several were already false in the direction of understating what's built, one gap this doc used to call closed had already been closed further. Headline changes:

- **Closed:** cassette-version-binding auto-enforcement at load (was the one residual Phase-2 gap; now called from `governance_harness.py`/`production_harness.py`); the twin's forged-cassette-snapshot gap (now compared directly, `twin_detector.py`'s `cassette_snapshot_forgery` sub-check); a real, previously-undisclosed auth gap (AC-13) where six twin-receiver routes accepted no credential at all.
- **New capture mechanism, live in the sense that it's tested and callable:** human review of a governance decision (`record_human_selection`, this session — see Recordkeeping & Audit Trail below).
- **New, substantial, but NOT wired into any live path:** a six-dimension bias/fair-lending screening framework (`regulatory_checks.py` + `regulatory_cassettes/cfpb_reg_b.py`) and a data-use/retention governance layer (`contract_*.py`). Both are extensively unit-tested against real scenarios. Neither `governance_harness.py`, `production_harness.py`, nor any API server constructs a `RegulatoryDeck` or a contract cassette anywhere. This is the same "architecturally supported, not shipped" posture the cassette-binding gap used to be in before it got closed — treat it the same way: real code, zero live effect until something calls it.

## Executive Summary

Sentinel OS is a fail-closed, auditable AI governance system for contact-center IVR platforms. Every governance decision is:
- **Logged** with full context: policy version, intent inference, reasoning, confidence, risk assessment
- ⚠️ **Chain-verified**, not tamper-proof, via SHA-256 chaining — see Known Limitations
- **Reviewable** by regulators using the provided audit playbook, which documents what it can and cannot prove
- ✅ **Traceable** to cassette-driven policy — the integrity hash now covers both the policy parameters *and* the decision code that runs on them (`cassette_code_hash`), with the source-hash scope caveat in Known Limitations

This manifest maps Sentinel's architecture to regulatory requirements across three jurisdictions.

## Known Limitations

Required by D1 alongside the claims below. Full detail and runnable verification: `AUDIT_PLAYBOOK.md`. Phase 2 closed six of the seven Phase-1 limitations at the code level (161/161 tests, verified live against real Postgres + the customer-witness twin). What each closure does and does not prove is stated below — the closures are as narrowly scoped as the original gaps.

**Closed since Phase 2 (through August 7, 2026):**

- ✅ **Cassette-version-binding enforcement is now auto-invoked at load.** Phase 2 shipped the binding mechanism, chain record, and content-mismatch refusal, but nothing called it automatically — a silent content change was detectable on demand, not rejected at load. `bind_cassette_version()` is now called from both `governance_harness.py` and `production_harness.py` at construction/swap time; a version re-bound with different content is refused loud, not just on-demand-checkable.
- ✅ **The twin now catches a forged policy snapshot with an otherwise-valid hash chain.** Phase 2 disclosed this as an open gap: a forged `cassette_snapshot` that left `cassette_hash`/`current_hash` untouched wasn't yet caught. `twin_detector.py` now compares the primary's `cassette_snapshot` against the replica's stored copy directly and flags a mismatch as `cassette_snapshot_forgery` — closing the gap the earlier disclosure named.
- ✅ **A real, previously-undisclosed twin-auth gap (AC-13) is closed.** Six `twin_receiver.py` routes — custody-event ingestion, obligation derivation/transition, and cohort-review read/write — accepted no credential at all. Each now enforces the same Bearer ship-token every other twin route already required. This was a genuine gap this document never disclosed because it wasn't known when the July 16 revision was written; it is closed now, not merely newly found.
- ✅ **A human's response to a governance decision can now be captured (F2, this session).** `PostgreSQLLedger.record_human_selection()` records `concur` / `override` / `escalate` against a real `governance_decision` row, hash-chained and twin-verified like every other record kind. The recommendation shown is looked up from the actual decision row, never accepted from the caller. Scope, deliberately: this is a capture mechanism, tested and callable — nothing in this repo's live path calls it yet (that requires a review UI or API surface this work did not build), and it feeds no learner (`simple_rl_trainer.py` is simulator-only, slated for GSA-815; wiring a real signal into training is a separate, later decision).

**Built but not yet invoked in the live path** (🔲 — same posture the cassette-binding gap above was in before it closed: real, tested code with zero effect until something constructs it):

- **A six-dimension bias/fair-lending screening framework** (`regulatory_checks.py`, reference profile in `regulatory_cassettes/cfpb_reg_b.py`): prohibited-basis/declared-proxy variable screening (always-on when the lens runs), input-authorization-tier and free-text-narrative screening (opt-in), and three cohort-level checks — statistical outcome equity, correlation-based proxy detection (catches a renamed proxy by its *values*, not just its name — the declared-name screens above are defeated by a rename; this one isn't, for numeric/boolean variables only, disclosed as a scope limit in the code itself), and geographic (ZIP/county) outcome equity. A separate escalation path (`RegulatoryDeck._cohort_equity_escalations`) can surface a flagged cohort review into a decision's own findings — always `ACTION_FLAG`, never `ACTION_BLOCK`, a documented design choice (never auto-block on a cohort-level signal). Extensively unit-tested. **Not constructed anywhere in `governance_harness.py`, `production_harness.py`, or any API entrypoint** — this document's July 16 claim of "no dedicated bias-testing mechanism" was true then and is now the wrong claim in the other direction: the mechanism exists, it's just inert until wired in.
- **Adverse-action reason-specificity screening** (`check_reason_specificity`, 12 CFR 1002.9): screens whether a stated outcome reason is case-specific or generic/boilerplate. Same lens, same not-yet-invoked status as above.
- **The cohort assembly job that feeds the three cohort-level dimensions** (`obligation_sweep.py`) has a CLI entrypoint, but the module's own comment states plainly that nothing in this repo schedules it — a cron job, k8s CronJob, or equivalent is a deployer's responsibility, not something shipped here.
- **BISG demographic estimation** (`bisg_estimator.py`, name+address → estimated race/ethnicity posterior): built, but deliberately disabled by default — gated behind `SENTINEL_BISG_RESEARCH_MODE=true`, off in every normal boot, "research mode, IP review pending" per its own commit. The live geographic and correlation-based dimensions above do not depend on it — they work from ZIP/county geography and raw variable-value correlation, not estimated demographics.
- **Data-use/retention governance** (`contract_cassette.py`, `contract_egress.py`, `contract_retention.py`, `contract_attestation.py`): tracks a counterparty's data-use contract — ingest, egress authorization, retention-horizon-vs-attested-deletion checking (absence of a deletion record is never read as compliance), and code-hash-bound attestation receipts. Same status: real, tested, not constructed in any live path. This capability isn't mapped to any of the three jurisdictions below yet — it's disclosed here as an inventory item, not a compliance claim.

**Closed in Phase 2** (carried forward, unchanged):

- ✅ **`cassette_version` is now content-bound.** Binding a version records its `cassette_hash` and `cassette_code_hash` as a `cassette_binding` entry *in the hash chain itself* (no sidecar registry — the ledger stays the single source of truth). Re-binding the same version string with different content is refused loud. (Phase 2's residual gap here — enforcement available but not yet auto-invoked at load — has since closed; see "Closed since Phase 2" above.)
- ✅ **The integrity hash now covers decision code.** A new `cassette_code_hash` field hashes the cassette's decision-logic source (scoring, intent-mapping) plus a declared allowlist of shared governance modules, and is committed to the canonical hash. Two cassettes with identical parameters but different logic now hash differently (verified live). Scope caveat: because cassettes are Turing-complete Python, a source hash covers the cassette's own code and its declared governance imports — not the full transitive dependency graph. It is a strong integrity ceiling, not a proof of semantic equivalence.
- ✅ **Governor input is now structurally fenced.** The governance instruction moves to the API `system` role; caller-supplied data is delivered in an XML-delimited, escaped `untrusted_caller_data` block. A caller value that forges the closing delimiter is escaped to inert text (verified live). This defends against prompt-level confusion; it does not defend against a compromised or backdoored model — model identity (below) is the forensic counterpart for that class.
- ✅ **Model identity is now recorded per decision.** Every governance decision records `model_identity` — the model string the API actually resolved to (`response.model`, the ground truth, not the requested alias) — committed to the canonical hash. Fail-closed governor paths record `None` (a decision that didn't come from a model claims no model). Forensics can now scope "which model governed decisions N–M."
- ✅ **Formal decision supersession now exists.** A reviewer can append a `decision_supersession` entry that references the original by ID *and by its `current_hash`* (proving they acted on the real decision), recording the superseding authority, reason, and corrected outcome. The original row is never altered — verified immutable after supersession. This is new evidence, not deletion or amendment.
- ✅ **Authorizing identity is now in every decision row.** Each decision records `authorized_by` — a role or service identity (e.g. `harness:production`), never a raw key and never PII — committed to the canonical hash. This is the identity foundation supersession builds on.

**Remaining (genuinely still open):**

- **Chain tamper-evidence is not tamper-proof against a true database-owner/superuser.** `verify_chain()` catches accidental corruption and naive in-place edits. The *application's own credential* can no longer disable the ledger's protective triggers — `ICEBERG_LEDGER_RUNTIME_USER` is required and independently verified to be non-owner, non-superuser at startup (see Recordkeeping & Audit Trail, below). What remains open is a human operator with direct, independent table-owner or superuser database access (e.g. a DBA), who could still disable the triggers and produce a rewritten or wiped chain that verifies clean *on the primary*. The customer-held witness twin (DAP v1, below) detects such tampering for rows already synced to a replica. `AUDIT_PLAYBOOK.md` Sections 1–3.
- **Correlation-based proxy detection (dimension 5 of the bias framework above) screens numeric/boolean variables only.** String/categorical variable values are not screened for correlation with protected-group membership — disclosed as a scope limit in the code itself, not yet closed.
- **The bias framework and the data-use/retention layer are both unwired from the live path** — see "Built but not yet invoked" above. This is the largest single gap between what this repo can do and what it currently does.

## EU AI Act (Enforcement August 2, 2026)

### Transparency & Explainability

**Requirement:** Providers must document AI system capabilities, limitations, and decision logic.

**Sentinel's Compliance:**
- ✅ **Cassette is policy for both parameters and logic:** Governance thresholds live in a single, human-readable configuration file, not hidden in code — regulators can read every parameter in minutes. The integrity hash now covers the decision code itself (`cassette_code_hash` over scoring/classification/reward logic and declared governance imports) in addition to the parameters; two cassettes with identical parameters and different logic now hash differently. Scope caveat (source-hash ceiling) in Known Limitations.
- ✅ **Intent reasoning documented:** Every decision record includes `intent_reasoning` and `intent_confidence`.
- ✅ **Governor reasoning and model identity documented:** Every decision includes `reasoning` from the governing model and `model_identity` — the model the API actually resolved to (`response.model`) — committed to the canonical hash.
- 📄 **Model card:** See `MODEL_CARD.md` (not independently re-verified in this revision).
- 📄 **Artifact:** `COMPLIANCE.md` (this file), `MODEL_CARD.md`, `AUDIT_PLAYBOOK.md`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.3.

### Risk Mitigation & Fail-Safe Defaults

**Requirement:** High-risk AI systems must have mitigation strategies and fail-safe defaults.

**Sentinel's Compliance:**
- ✅ **Fail-closed on all errors:** verified live, 11/11 adversarial governor error paths — no API client, JSON parse failure, timeout, or type-validation failure ever produces an approval.
- ✅ **Human review with formal supersession:** decisions are human-readable at any time, and a reviewer with authority can record a formal `decision_supersession` within Sentinel — referencing the original by hash, recording authority/reason/corrected outcome, leaving the original row immutable. Disagreement is now captured *inside* the record, not outside it.
- ✅ **Human concur/override/escalate is now captured too, separately from supersession:** `record_human_selection` (F2) records a reviewer's response to the governor's own verdict — narrower than a formal supersession (no corrected outcome, no authority claim), for the lighter-weight case of "a human looked at this and agreed/disagreed/routed it elsewhere." No live caller triggers this yet; see Known Limitations.
- ✅ **Real-time monitoring:** Prometheus metrics track decision rate, approval rate, error rate, rejection rate.
- ✅ **Transparent logging:** every decision is logged; nothing is silent.
- 📄 **Code Reference:** `claude_governance_api.py`, `production_harness.py`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.1.

### Bias & Fairness

**Requirement:** Document fairness assessment and mitigate discriminatory outcomes.

**Sentinel's Compliance:**
- ✅ **No protected attributes in decision:** intent classification is a queue-based rule lookup (confirmed: not a machine-learning model), not derived from race, gender, age, accent, or any protected attribute.
- ✅ **Cassette parameters are observable and tunable:** thresholds are all in one place, reviewable by audit teams.
- ✅ **Decision audit trail:** every decision records the `cassette_version` that governed it, content-bound to its `cassette_hash`/`cassette_code_hash` via a `cassette_binding` chain entry, and that binding is now auto-enforced at cassette load (see Known Limitations — this closed since the last revision).
- 🔲 **A six-dimension bias/fair-lending screening framework exists, built and tested, but is not invoked from the live decision path.** Declared-proxy and prohibited-basis variable screening, input-authorization-tier and narrative-legitimacy screening (both opt-in), and three cohort-level checks (statistical outcome equity, value-correlation-based proxy detection, geographic ZIP/county outcome equity) — see `regulatory_checks.py` and `regulatory_cassettes/cfpb_reg_b.py`, and Known Limitations above for exactly what's disclosed as unscreened (string/categorical proxies) and what's simply not wired in yet.
- 📄 **Bias audit framework:** See `MODEL_CARD.md` (not independently re-verified in this revision).

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.5 ("No Hidden Branching").

### Recordkeeping & Audit Trail

**Requirement:** Maintain verifiable audit trail of decisions and rationale.

**Sentinel's Compliance:**
- ⚠️ **SHA-256 chain:** every ledger entry hashes a canonical form of its own content and links to the previous entry's hash. This catches accidental corruption and naive in-place edits. It does not prove an operator with database access hasn't rewritten history consistently — see Known Limitations and `AUDIT_PLAYBOOK.md` Sections 1–3.
- ⚠️ **Forensic reconstruction:** pull any decision ID and see the cassette version, caller journey, friction count, intent classification, governor input/output/reasoning, and approval decision — the cassette version is content-bound (see above).
- ✅ **Append-only by trigger, AND by connection privilege:** PostgreSQL triggers block ordinary UPDATE/DELETE/TRUNCATE, and advisory locks make concurrent writes clean (verified live, 50/50, no forks). The app now refuses to start unless `ICEBERG_LEDGER_RUNTIME_USER` is explicitly set to a restricted, non-owner role — there is no privileged fallback. A startup check independently confirms the resolved identity is neither the table owner nor a superuser and refuses to boot if it is, even when the variable is set but misconfigured. This closes the app-credential path to H3/H5 structurally, not just by convention; see `ledger_immutability.sql`'s `ledger_reader` role and `PostgreSQLLedger._verify_runtime_user_is_not_privileged`.

#### External Witness — DAP v1 (Divergence Attestation Protocol)

Sentinel ships a customer-held-key witness replica ("the twin") that turns the primary-alone tamper-evidence gap into detectable-with-scope. It is implemented and tested; it is **off by default** (no shipping unless `TWIN_TARGETS_FILE` is configured) and its acceptance as a *formal* regulatory control has not been granted.

**What it proves.** The twin is a read-only tailer that ships each ledger row, encrypted, to a store the customer controls with a key **Sentinel does not hold** (X25519 + HKDF + AES-256-GCM; Sentinel-cannot-decrypt is proven three ways in the test suite). The customer's receiver is itself append-only and hash-chained. A detector compares the live primary against the customer's replica and returns a verdict per row: `MATCH`, `DIVERGE` (with cause: `clear_hash_mismatch` / `envelope_unopenable` / `payload_hash_mismatch`), `MISSING`, `EXTRA`, or `PENDING`. Verified live: a full chain rewrite via the app's own DB credential is flagged `clear_hash_mismatch` on every downstream row; a wipe is flagged `EXTRA` (present on replica, absent on primary). A separate Independent Completeness Cross-check (ICC) compares against the customer's *own* submission record to catch drops that happened before the ledger ever saw them.

**What it does not prove.** The twin only knows what the primary told it. It protects rows **already synced to a replica before** a tamper occurs (covered-window scoping) — tampering a row before its first sync would look like an honest `MATCH` once it ships. It is not a real-time block; it is after-the-fact attestation. It now does cross-check live `cassette_snapshot` *content* against the replica's stored copy (`twin_detector.py`'s `cassette_snapshot_forgery` sub-check, closed since the previous revision of this document) — a forged snapshot that leaves `cassette_hash`/`current_hash` untouched is caught.

**How a regulator runs it.** `twin_probe.py` is a one-command conformance probe: pointed at a primary and a customer replica, it exercises the detector across the verdict space and prints an attestation of what matched, what diverged, and the covered window — without needing Sentinel-side key material. See `AUDIT_PLAYBOOK.md` for the exact invocation and how to read each verdict.
- 📄 **Schema Reference:** `governance/ledger_postgres.py`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Sections 1–3.

## NAIC AI Model Bulletin (Insurance Context)

### Explainability

**Requirement:** "Explainability and interpretability of model outputs are required."

**Sentinel's Compliance:**
- ✅ **Every decision includes reasoning:** `intent_reasoning` and governor `reasoning`.
- ⚠️ **Cassette parameter metadata:** each parameter carries `justification`, `approval_date`, and `last_reviewed` fields. `justification` is populated with a real explanation for every parameter in both shipped cassettes. `approval_date` and `last_reviewed` are `None` for every parameter in both — there is no recorded approval workflow yet. Three of the banking cassette's justifications explicitly self-flag as unreviewed placeholders. Treat the "why" as documented; treat "reviewed and approved" as not yet true.
- ✅ **Intent classifier is transparent:** queue name → intent label is a direct lookup, not a black box.
- 🔲 **Adverse-action reason specificity** (12 CFR 1002.9-style: a stated reason must be case-specific, not boilerplate): `check_reason_specificity` in `regulatory_checks.py` screens exactly this — built and tested, not invoked from the live decision path. See Known Limitations.
- ✅ **A human's response to a decision can be recorded:** `record_human_selection` (F2) captures concur/override/escalate against the real decision row, hash-chained. Capture mechanism only — see Known Limitations for what still has to be built to actually trigger it in practice.
- 📄 **Artifact:** every decision record; `MODEL_CARD.md` (not independently re-verified in this revision).

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.2.

### Auditability

**Requirement:** "Systems must be subject to ongoing review and testing."

**Sentinel's Compliance:**
- ✅ **Audit playbook provided:** `AUDIT_PLAYBOOK.md` contains runnable SQL and an explicit account of what each check does and doesn't prove.
- ⚠️ **Forensic reconstruction:** available per decision ID; the cassette version is content-bound (see above).
- ✅ **Policy versioning:** `cassette_version` is stored with every decision and content-bound to the cassette's parameter and code hashes via a `cassette_binding` chain entry, so "what governed this decision" is anchored to verifiable content rather than a bare label.
- ✅ **Continuous monitoring:** Prometheus metrics show decision patterns over time.
- 📄 **Artifact:** `AUDIT_PLAYBOOK.md`, Prometheus dashboard

**Regulatory Verification:** `AUDIT_PLAYBOOK.md`, "Before You Start."

## TCPA Compliance (Collections & Telecom)

### Governance & Consent

**Requirement:** "Dial only compliant numbers. Never call when no consent. Respect do-not-call requests."

**Sentinel's Compliance:**
🔲 **Architectural fit, not shipped.** Sentinel's cassette pattern — externalized parameters, fail-closed defaults, full audit logging — is domain-agnostic; nothing about the governor or ledger is IVR-specific. In principle, a TCPA-specific cassette encoding consent, time-of-day, and call-frequency rules could reuse the same governor, ledger, and audit playbook described throughout this document.

**What ships today:** `ivr_cassette.py` and `banking_cassette.py`. No dialer, consent, do-not-call, or outbound-calling logic exists anywhere in this codebase. Sentinel currently governs inbound IVR interactions; TCPA governs outbound dialing — a distinct workflow this system does not yet implement. A regulator evaluating Sentinel for a TCPA use case today is evaluating an architecture pattern, not a deployed control.

**Regulatory Verification:** None available — there is nothing to run. Building a `tcpa_compliance_cassette.py` and the outbound-dialer integration it would require is a prerequisite, not a verification step.

## Sentinel's Compliance Posture: Summary Table

| Requirement | Status | Evidence | Audit Reference |
|---|---|---|---|
| Decision logging | ✅ | Ledger schema stores every decision | `AUDIT_PLAYBOOK.md` §7.2 |
| Policy transparency (parameters) | ✅ | Cassette is config, not code | `cassettes/ivr_cassette.py` |
| Policy transparency (decision logic) | ✅ | `cassette_code_hash` covers decision code + declared governance imports (source-hash ceiling) | Known Limitations |
| Explainability | ✅ | Intent + friction + reasoning in record | `AUDIT_PLAYBOOK.md` §7.2 |
| Auditability | ✅ | Reconstruction queries provided | `AUDIT_PLAYBOOK.md` |
| Fail-safe defaults | ✅ | 11/11 error paths verified live → `approved=false` | `AUDIT_PLAYBOOK.md` §7.1 |
| Error handling | ✅ | No silent failures; all logged | `AUDIT_PLAYBOOK.md` §7.4 |
| Human override (formal supersession) | ✅ | `decision_supersession` chain entry, links original by hash, original immutable | Known Limitations |
| Human selection capture (concur/override/escalate) | ✅ built, 🔲 unwired | `record_human_selection` (F2); capture works, nothing calls it live yet | Known Limitations |
| Tamper evidence | ⚠️ | Catches accidental/naive edits; not operator-proof | `AUDIT_PLAYBOOK.md` §1–3 |
| Cassette version integrity | ✅ | Content-bound via `cassette_binding` chain entry; auto-enforced at load | Known Limitations |
| Model identity per decision | ✅ | `model_identity` (`response.model`) in canonical hash; `None` on fail-closed paths | Known Limitations |
| Authorizing identity | ✅ | `authorized_by` (role/service, never PII) in canonical hash | Known Limitations |
| Governor input isolation | ✅ | System-role instruction + escaped delimited untrusted-data block | Known Limitations |
| Bias / fair-lending screening (6 dimensions) | 🔲 | Built and tested (`regulatory_checks.py`, `cfpb_reg_b.py`); not invoked from any live path | Known Limitations |
| Adverse-action reason specificity | 🔲 | `check_reason_specificity`, built and tested; not invoked from any live path | Known Limitations |
| Data-use / retention governance | 🔲 | Contract cassettes (ingest/egress/retention/attestation); built and tested; not invoked from any live path | Known Limitations |
| Twin forged-snapshot detection | ✅ | `cassette_snapshot` compared directly, primary vs. replica | Known Limitations |
| Twin route authentication | ✅ | All twin-receiver routes now require ship-token auth (AC-13 closed) | Known Limitations |
| Model card | 📄 | Not independently re-verified | `MODEL_CARD.md` |
| Incident response | ✅ | Structured write-failure handling, fail-closed | `production_harness.py` |
| TCPA / outbound dialing | 🔲 | No implementation exists | — |

## Monitoring & Incident Response

### Real-Time Metrics
- **Decision rate**, **approval rate**, **error rate** (target <1%), **rejection rate** — all exported to Prometheus. Grafana dashboards in `Deploy/grafana/`.

### Incident Response
- **Governor timeout:** fails closed (`approved: false`).
- **API key invalid:** logged as error; subsequent calls fail closed.
- ⚠️ **Database unavailable:** a ledger write failure returns a structured failure and is logged — it does not silently approve and does not buffer in memory for later flush. A decision that can't be written is not recorded as approved, but it is also not automatically retried; check the structured-failure log, don't assume eventual consistency.
- **Ledger content-hash mismatch:** flagged by `verify_chain()` — see Known Limitations for what this check does and doesn't rule out.

## How Regulators Will Use This

**Day 1:** Read `COMPLIANCE.md` — architecture and compliance claims, including limitations.
**Day 2:** Read `MODEL_CARD.md` — classifier limitations and bias assessment.
**Day 3:** Run `AUDIT_PLAYBOOK.md` — verify what can be checked; understand what can't be.
**Day 4:** Interview findings — ask the Section 5 Q&A checklist questions directly.

Audit duration depends on scope and follow-up; no fixed estimate is claimed here.

## Contact & Support

- **Compliance questions:** `AUDIT_PLAYBOOK.md` or run the provided queries.
- **Policy questions:** review cassette files in `cassettes/`.
- **Incident reports:** Prometheus metrics or the PostgreSQL ledger directly.
- **Documentation updates:** all compliance docs are in the repo root, version-controlled.
