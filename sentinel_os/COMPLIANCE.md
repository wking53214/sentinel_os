# Sentinel OS: Regulatory Compliance Manifest

**Last Updated:** July 16, 2026
**Status:** Production-Deployed — Phase 1 architecture (ledger is self-contained, not externally anchored; see Known Limitations)
**Enforcement Dates:** EU AI Act (Aug 2, 2026) | NAIC Bulletin (effective 2026)
**Legend:** ✅ verified, holds as stated · ⚠️ true with a material qualification below · 🔲 architecturally supported, not shipped

## Executive Summary

Sentinel OS is a fail-closed, auditable AI governance system for contact-center IVR platforms. Every governance decision is:
- **Logged** with full context: policy version, intent inference, reasoning, confidence, risk assessment
- ⚠️ **Chain-verified**, not tamper-proof, via SHA-256 chaining — see Known Limitations
- **Reviewable** by regulators using the provided audit playbook, which documents what it can and cannot prove
- ⚠️ **Traceable** to cassette-driven policy parameters — the integrity hash covers those parameters, not the decision code that runs on them

This manifest maps Sentinel's architecture to regulatory requirements across three jurisdictions.

## Known Limitations (Phase 1)

Required by D1 alongside the claims below. Full detail and runnable verification: `AUDIT_PLAYBOOK.md`.

- **Chain tamper-evidence is not tamper-proof.** `verify_chain()` catches accidental corruption and naive in-place edits. An operator with database access — table-owner privilege, not superuser — can disable the ledger's protective triggers and produce a rewritten or fully wiped chain that still verifies clean. `AUDIT_PLAYBOOK.md` Sections 1–3.
- **`cassette_version` is self-asserted.** Nothing currently binds that label to the parameters or code that actually governed a decision.
- **The integrity hash covers parameters, not code.** Two cassettes with identical parameters but different decision logic produce an identical hash.
- **No structural defense against injected caller input** in governor prompt assembly (no delimiter or role separation). Verified sound against tested attacks today — that's a property of the model's judgment, not the architecture.
- **Model identity isn't recorded per decision.** Which model version served a given decision isn't in the ledger.
- **No formal decision-reversal mechanism exists.** Decisions are human-reviewable but not supersedable within Sentinel itself.

Phase 2 ("Witness") is scoped to close the first four. Timeline: not yet scheduled — `AUDIT_PLAYBOOK.md` Section 4.

## EU AI Act (Enforcement August 2, 2026)

### Transparency & Explainability

**Requirement:** Providers must document AI system capabilities, limitations, and decision logic.

**Sentinel's Compliance:**
- ⚠️ **Cassette is policy for parameters, not for logic:** Governance thresholds live in a single, human-readable configuration file, not hidden in code — regulators can read every parameter in minutes. What the cassette hash does *not* cover is the decision code itself (scoring, classification, reward logic); two cassettes with identical parameters and different code hash identically. See Known Limitations.
- ✅ **Intent reasoning documented:** Every decision record includes `intent_reasoning` and `intent_confidence`.
- ⚠️ **Governor reasoning documented:** Every decision includes `reasoning` from the governing model. Which model version served that decision isn't currently recorded — see Known Limitations.
- 📄 **Model card:** See `MODEL_CARD.md` (not independently re-verified in this revision).
- 📄 **Artifact:** `COMPLIANCE.md` (this file), `MODEL_CARD.md`, `AUDIT_PLAYBOOK.md`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.3.

### Risk Mitigation & Fail-Safe Defaults

**Requirement:** High-risk AI systems must have mitigation strategies and fail-safe defaults.

**Sentinel's Compliance:**
- ✅ **Fail-closed on all errors:** verified live, 11/11 adversarial governor error paths — no API client, JSON parse failure, timeout, or type-validation failure ever produces an approval.
- ⚠️ **Human review, not formal reversal:** decisions are stored in the ledger and human-readable at any time. There is currently no built-in mechanism to supersede or annul a past decision within Sentinel — a reviewer who disagrees acts outside the system, not through a Sentinel override function.
- ✅ **Real-time monitoring:** Prometheus metrics track decision rate, approval rate, error rate, rejection rate.
- ✅ **Transparent logging:** every decision is logged; nothing is silent.
- 📄 **Code Reference:** `claude_governance_api.py`, `production_harness.py`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.1.

### Bias & Fairness

**Requirement:** Document fairness assessment and mitigate discriminatory outcomes.

**Sentinel's Compliance:**
- ✅ **No protected attributes in decision:** intent classification is a queue-based rule lookup (confirmed: not a machine-learning model), not derived from race, gender, age, accent, or any protected attribute.
- ✅ **Cassette parameters are observable and tunable:** thresholds are all in one place, reviewable by audit teams.
- ⚠️ **Decision audit trail:** every decision records the `cassette_version` that governed it, but that label is self-asserted — see Known Limitations before treating historical version comparisons as authoritative.
- 📄 **Bias audit framework:** See `MODEL_CARD.md` (not independently re-verified in this revision).

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.5 ("No Hidden Branching").

### Recordkeeping & Audit Trail

**Requirement:** Maintain verifiable audit trail of decisions and rationale.

**Sentinel's Compliance:**
- ⚠️ **SHA-256 chain:** every ledger entry hashes a canonical form of its own content and links to the previous entry's hash. This catches accidental corruption and naive in-place edits. It does not prove an operator with database access hasn't rewritten history consistently — see Known Limitations and `AUDIT_PLAYBOOK.md` Sections 1–3.
- ⚠️ **Forensic reconstruction:** pull any decision ID and see the cassette version, caller journey, friction count, intent classification, governor input/output/reasoning, and approval decision — with the same self-asserted-version caveat as above.
- ⚠️ **Append-only by trigger, not by design guarantee:** PostgreSQL triggers block ordinary UPDATE/DELETE/TRUNCATE, and advisory locks make concurrent writes clean (verified live, 50/50, no forks). Those triggers are disableable by the table-owning role, which is the role the application uses unless `ICEBERG_LEDGER_RUNTIME_USER` is explicitly configured to the restricted `ledger_reader` role — it is not configured by default.
- 📄 **Schema Reference:** `governance/ledger_postgres.py`

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Sections 1–3.

## NAIC AI Model Bulletin (Insurance Context)

### Explainability

**Requirement:** "Explainability and interpretability of model outputs are required."

**Sentinel's Compliance:**
- ✅ **Every decision includes reasoning:** `intent_reasoning` and governor `reasoning`.
- ⚠️ **Cassette parameter metadata:** each parameter carries `justification`, `approval_date`, and `last_reviewed` fields. `justification` is populated with a real explanation for every parameter in both shipped cassettes. `approval_date` and `last_reviewed` are `None` for every parameter in both — there is no recorded approval workflow yet. Three of the banking cassette's justifications explicitly self-flag as unreviewed placeholders. Treat the "why" as documented; treat "reviewed and approved" as not yet true.
- ✅ **Intent classifier is transparent:** queue name → intent label is a direct lookup, not a black box.
- 📄 **Artifact:** every decision record; `MODEL_CARD.md` (not independently re-verified in this revision).

**Regulatory Verification:** `AUDIT_PLAYBOOK.md` Section 7.2.

### Auditability

**Requirement:** "Systems must be subject to ongoing review and testing."

**Sentinel's Compliance:**
- ✅ **Audit playbook provided:** `AUDIT_PLAYBOOK.md` contains runnable SQL and an explicit account of what each check does and doesn't prove.
- ⚠️ **Forensic reconstruction:** available per decision ID, with the self-asserted-version caveat noted above.
- ⚠️ **Policy versioning:** `cassette_version` is stored with every decision; auditing "what governed this decision" is only as reliable as that self-asserted label.
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
| Policy transparency (decision logic) | ⚠️ | Hash covers parameters, not code | Known Limitations |
| Explainability | ✅ | Intent + friction + reasoning in record | `AUDIT_PLAYBOOK.md` §7.2 |
| Auditability | ✅ | Reconstruction queries provided | `AUDIT_PLAYBOOK.md` |
| Fail-safe defaults | ✅ | 11/11 error paths verified live → `approved=false` | `AUDIT_PLAYBOOK.md` §7.1 |
| Error handling | ✅ | No silent failures; all logged | `AUDIT_PLAYBOOK.md` §7.4 |
| Human override | ⚠️ | Reviewable; no formal reversal mechanism | Known Limitations |
| Tamper evidence | ⚠️ | Catches accidental/naive edits; not operator-proof | `AUDIT_PLAYBOOK.md` §1–3 |
| Cassette version integrity | ⚠️ | Self-asserted, not bound to content | Known Limitations |
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
