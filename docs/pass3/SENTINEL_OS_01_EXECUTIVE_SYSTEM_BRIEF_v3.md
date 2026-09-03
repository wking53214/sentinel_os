> **⚠ Point-in-time snapshot — not maintained.** Documented baseline `68cadfb` (July 29, 2026). Since then PRs #28–30 landed: keyed `authorized_by` ledger attestation with key rotation, the persisted observed-event layer, and — most relevant to anything below that names a directory — the **extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo** (2026-08-28). The standalone simulator and its `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree, Twilio ingestion, the Claude governor client, and the queue/staffing/Bayes layer are no longer in this repo. Treat directory maps, module inventories, and test counts here as historical. The canonical current description is the [repository root README](../../README.md).

---

# DOCUMENT 1 — EXECUTIVE SYSTEM BRIEF

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification of every statement in this document:**

| Tag | Meaning |
|---|---|
| `FACT` | Stated directly in a source document |
| `DERIVED` | Follows logically from two or more documented facts |
| `INTERPRETATION` | A reasonable reading of the system's apparent role; not established by the sources |
| `UNKNOWN` | The source documents do not contain this information |

No statement in this document draws on knowledge outside the four source documents.

---

## 0. READER FRAME

**AUDIENCE**
Chief executive, chief technology officer, enterprise technology executive, business decision maker. No engineering background assumed.

**READER QUESTIONS**
1. What is this thing, in one sentence?
2. Is it real, or is it a design?
3. What does it do that we cannot already do?
4. What liability or exposure does it touch?
5. If we adopted it, where would it sit and who would run it?
6. What would we still have to find out before committing money or people?

**DECISION OBJECTIVE**
Decide whether Sentinel OS warrants a formal technical and commercial evaluation — and, if so, what that evaluation must establish. This document does not support a purchase, build, or deployment decision, because the source material does not contain the evidence such a decision requires (see §9).

**TRUST FAILURE**
This reader loses confidence if they later discover any of the following were true but unstated:

- `FACT` The phrase "production-ready" appears in the source material, but no source document records a live deployment, real end users, production traffic, or a paying customer. Named plainly in §8 and §9.
- `FACT` The system's own governance verdict does not currently control any behavior. A separate quality score still drives routing; the verdict is recorded alongside it. Named plainly in §7.
- `FACT` The largest external data source the system is built to consume is not connected. Named plainly in §7.
- `FACT` One named individual is the only human role documented anywhere in the sources. Named plainly in §4.

Each is disclosed in this brief rather than discovered later.

---

## 1. WHAT CATEGORY OF SYSTEM IS THIS?

`FACT` The source summary describes what exists as "a production-ready governance kernel with tamper-evident dual-ledger (primary + sealed twin replica)."

`FACT` No source document contains a canonical mission statement, product definition, or charter for Sentinel OS. The Technical Architecture Map states this explicitly and declines to supply one.

`DERIVED` From the module inventory, the data model, and the runtime flow, the system's function can be described without a charter: Sentinel OS records automated decisions, stores them so they cannot be quietly altered, labels how reliable each underlying fact was, checks decisions against compliance rules, and tracks whether the eventual real-world outcome was ever recorded.

`INTERPRETATION` In plain business terms, this is closer to an accounting system for automated decisions than to a decision-making system. Its apparent purpose is not to decide better, but to make decisions examinable afterward by someone who does not trust the system that made them.

`INTERPRETATION` The nearest familiar analogue is a general ledger. A general ledger does not earn revenue; it makes revenue claims auditable. Sentinel OS occupies that position relative to automated decisions.

`UNKNOWN` Whether the builder intends it as a product, a platform, an internal control, or a research artifact is not stated in any source document.

---

## 2. WHAT PROBLEM DOES IT APPEAR DESIGNED TO ADDRESS?

The design choices that are recorded as locked and non-negotiable indicate the problem more reliably than any description would. Four of them:

`FACT` **The record cannot be edited.** The decision ledger is append-only, enforced inside the database itself by a trigger that runs before every insert. Rows are cryptographically chained: each row carries a hash of itself and a hash of the row before it.

`FACT` **A second party holds an independent copy.** A "twin" replica is held under a separate database identity, sealed with encryption (X25519 + AES-GCM). Chain integrity is recomputed independently at three separate points — the writer, the verifier, and the twin — and a new record type is not treated as complete until all three agree.

`FACT` **The system does not believe the actor's own account.** What the acting system reports about itself is cross-checked against observed data, and any discrepancies are logged. Where a recorded outcome does not match what was expected, a written reason is mandatory; validation fails if it is absent.

`FACT` **Every claim carries a label for how it is known.** Each observed fact is stamped VERIFIED, ATTESTED, or ESTIMATED. The governing rule recorded in the code reads: "Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."

`DERIVED` These four choices only make sense against a specific problem: an organization must later prove what an automated system decided, on what basis, and how confident that basis was — to a party who assumes the organization may have an incentive to shade the answer.

`INTERPRETATION` The problem being addressed is therefore not "our AI makes mistakes." It is "we cannot currently prove what our AI did, and the proof will be demanded by someone who is not on our side."

---

## 3. WHY MIGHT THIS CAPABILITY MATTER?

`FACT` The reference compliance module implemented in the repository is a CFPB Reg B lens. The statistical test it applies is the four-fifths rule, identified in the sources as a CFPB / ECOA standard for disparate impact. The repository carries a `COMPLIANCE.md` regulatory baseline file and a `MODEL_CARD.md` system card.

`FACT` The first cassette built to track real-world outcomes is a mortgage lending cassette. The worked example in the sources opens a loan decision obligation with a three-year maturity horizon.

`DERIVED` The system is aimed, in its first concrete application, at consumer lending decisions — a category where the obligation to explain an adverse decision is a legal requirement rather than a preference.

`INTERPRETATION` Three properties are unusual enough to be worth an executive's attention:

1. **Distrust is structural, not procedural.** Most audit logging can be edited by whoever holds the credentials. Here the prohibition sits in the database and in a copy held by a different identity. The control does not depend on the honesty of the person running it.
2. **Uncertainty is recorded rather than smoothed over.** Systems commonly present estimates and measurements in the same typeface. This one refuses to, and requires an unresolved item to state why it is unresolved and what would close it.
3. **Outcomes are tracked past the decision.** A loan decision opens a record that stays open for years until the actual result is entered or formally abandoned. The decision and its consequence have separate lifespans by design.

`INTERPRETATION` The commercial significance, if any, rests on whether examiners and regulators come to demand this class of evidence. The source documents contain no evidence on that question either way.

---

## 4. WHAT ORGANIZATIONAL RISK DOES IT RELATE TO?

**Risks the system is built to address**

`DERIVED` Inability to reconstruct why an automated decision was made, when challenged after the fact.
`DERIVED` Disputed records — an audit trail that the organization can technically alter, and therefore cannot fully rely on as a defense.
`DERIVED` Discriminatory outcomes in regulated decisions, addressed through six compliance dimensions covering prohibited inputs, input authorization, stated-reason legitimacy, statistical outcome disparity, correlation-based evasion, and geographic disparity.
`DERIVED` Silent substitution of decision logic: the code that made a decision is hashed and bound at recording time, and a mismatch raises a version-conflict error rather than proceeding.

**Risks the system introduces or does not cover**

`FACT` Two of the six compliance dimensions are opt-in and off unless configured. A third — geographic equity — is implemented but not connected to the live evaluation path. The sources state the consequence in their own words: redlining is "not prevented in real decisions."

`FACT` Compliance evaluation is currently cohort-level and after the fact. The per-decision wiring exists as a function but is documented as "not called from live judge()."

`FACT` One named individual is the only documented human role in the system. He applies patches, sets cassette scope, and makes governance calls. Three prepared patches are recorded as waiting on his review. Whether any other operator role exists is stated as not known.

`DERIVED` This is a concentration risk. Under the sources as written, the system's change control, deployment decisions, and governance judgments all route through a single person.

`VERIFIED` (corrected in v3) This document originally stated that free-text reasoning is never captured at decision time. A subsequent verification pass against the live repository found this false: a `reason` column is populated on every decision row. What it holds, however, is the deciding AI's own self-reported reasoning — the same kind of self-report the kernel's design distrusts and cross-checks elsewhere — not an independently supplied business reason. The narrower, accurate gap is that no independent business-reason field exists.

`FACT` Renaming a variable defeats two of the six screens. The sources disclose this as a genuine design-level gap requiring a new approach, not a bug.

`INTERPRETATION` The disclosure pattern above is itself notable. The system's own documentation names its failures in the same register as its capabilities, which is the behavior an examiner would expect from a control system and is uncommon in vendor material.

---

## 5. WHERE DOES SENTINEL FIT IN AN ENTERPRISE ENVIRONMENT?

`FACT` The system runs as three entry points: a worker that ingests decision records, an HTTP API server offering judge, explain, and ledger-query endpoints, and a standalone batch simulator that requires no database.

`FACT` It requires PostgreSQL 16 and Redis 7. It ships a Dockerfile and a docker-compose definition covering both services plus the API. Its production runtime identity is a restricted database role holding only SELECT and INSERT permissions — it cannot update or delete. If that identity is unset, or is the table owner or a superuser, the system refuses to start.

`DERIVED` Architecturally it sits beside the systems that make decisions, not in front of them. Decisions and observations flow in; sealed evidence and compliance findings flow out to storage and to the twin.

`INTERPRETATION` In an enterprise organization chart it would be owned jointly rather than cleanly: engineering runs it, but its output is consumed by compliance, risk, internal audit, and eventually external examiners. That split ownership is a real adoption obstacle and is worth naming early — a system whose users are auditors but whose operators are engineers has no obvious internal budget owner.

`UNKNOWN` No source document describes integration with any identity provider, SIEM, data warehouse, GRC platform, or enterprise monitoring stack. No OpenAPI or Swagger specification exists. There is no documented multi-tenancy, access-control model beyond the database roles, or user interface of any kind.

---

## 6. THE FIVE TERMS AN EXECUTIVE WILL HEAR

| Term | Plain meaning | Status in the sources |
|---|---|---|
| **Governance** | The rules the system enforces on itself about what must be recorded and what may not be changed. Eight are documented as locked and code-enforced. | `FACT` Implemented, with each rule traced to a named enforcement point |
| **Evidence** | The append-only, hash-chained decision ledger, plus the independently-held sealed copy against which it can be checked. | `FACT` Implemented |
| **Provenance** | The VERIFIED / ATTESTED / ESTIMATED label carried by every fact, so a reader can tell a measurement from a guess. | `FACT` Implemented |
| **Cassettes** | Interchangeable plug-in modules holding the knowledge of one business domain, so the core system never needs to know what industry it is running in. Three exist: call center (reference), banking, mortgage. | `FACT` Implemented |
| **Regulatory lenses** | Read-only compliance views that examine a decision and raise findings, but cannot alter it. One exists: a CFPB Reg B reference lens. | `FACT` Implemented |

`FACT` The separation is enforced, not merely intended: the core kernel imports no cassette code, and a cassette that declares a setting belonging to a capability it has not switched on is refused at load time rather than at run time.

`INTERPRETATION` The business claim implied by this structure is that one governance engine can serve many industries by swapping the domain module, without reopening the audited core. That claim is structurally supported by the code organization and is not yet demonstrated by adoption in more than one live industry.

---

## 7. IMPLEMENTED CAPABILITY

Legend: 🟢 operational as documented · 🟡 built but partial or not connected · ⚪ placeholder or stub

| Capability | Status | What the sources establish |
|---|---|---|
| Append-only decision ledger | 🟢 | `FACT` Enforced by a database trigger, not application code; rows hash-chained |
| Independent twin replica | 🟢 | `FACT` Separate database identity, sealed envelope, three independent chain recomputations that must agree |
| Chain verification on demand | 🟢 | `FACT` A single documented call returns a clean/dirty result plus findings |
| Provenance stamping | 🟢 | `FACT` VERIFIED / ATTESTED / ESTIMATED on every observation |
| Outcome obligation tracking | 🟢 | `FACT` OPEN → RESOLVED or ABANDONED, with a declared maturation rule and expected-by date |
| Domain pluggability | 🟢 | `FACT` Three cassettes on one interface; kernel imports no cassette code |
| Tamper detection on decision logic | 🟢 | `FACT` Code-hash binding; mismatch produces a version-conflict error |
| Fail-closed startup | 🟢 | `FACT` Refuses to run on an unset or over-privileged database identity |
| Compliance dimensions 1, 4, 5 | 🟢 | `FACT` Prohibited-input screen, statistical outcome equity, correlation-based evasion detection — all default-on |
| Compliance dimensions 2, 3 | 🟡 | `FACT` Implemented, opt-in, off unless explicitly configured |
| Compliance dimension 6 (geographic) | 🟡 | `FACT` Implemented, not wired to the live evaluation path; patch prepared and unapplied |
| Per-decision compliance evaluation | 🟡 | `FACT` The retrieval function exists but is not called from the live judge path |
| Kernel verdict driving behavior | 🟡 | `FACT` It does not. A separate quality score still controls routing; the verdict is recorded alongside it by deliberate design |
| Live event ingestion | ⚪ | `FACT` The source-agnostic contract is defined and tested; no real event source is connected. The Twilio stream fetch returns an empty list |
| AI-generated explanation fallback | ⚪ | `FACT` Present in code, returns a hardcoded value |
| Reinforcement learning | ⚪ | `FACT` The untrained module was removed; real training is explicitly on hold |

**Verification signals recorded in the sources**

`FACT` Roughly 670 tests pass with 6 skipped, confirmed twice back-to-back. Linting reports zero findings on a pinned version. Security scanning reports zero findings at medium severity and above — reached by annotating and justifying seventeen medium findings and one high finding, not by their absence. Continuous integration first ran fully green on July 24 and the last five recorded runs were green. One test is known to be flaky under load. `VERIFIED` (corrected in v3) This document originally stated that the eighteen twin-replica tests are excluded from automated runs. That is stale: commit `d881bc0` added the infrastructure needed to run them in CI, and they now run there on every commit.

---

## 8. BUSINESS INTERPRETATION

Everything in this section is `INTERPRETATION` and is not established by the source documents.

**What the architecture appears to be betting on.** That evidentiary obligations around automated decisions will become concrete enough that organizations must produce records to a hostile reader, and that retrofitting such records onto systems not built for it will be harder than adopting a purpose-built layer.

**Why the domain-blind core may be the substantive choice.** Keeping industry knowledge out of the audited core means the expensive, hard-to-change part — immutability, chaining, custody separation, provenance — is written once. If the separation holds, adding an industry is a cassette rather than a re-audit. The code organization supports this; nothing in the sources demonstrates it at scale, since only one cassette currently exercises the outcome-tracking path.

**Where the design shows unusual discipline.** Refusing to let the governance verdict drive behavior is a costly choice that makes the system less impressive in a demonstration and more defensible in an examination — the governance layer cannot be blamed for an operational outcome it never caused. The same discipline appears in the mandatory-reason rule and in the refusal to let unresolved states be a vague catch-all.

**Where the business case is thinnest.** The system produces evidence for auditors but is operated by engineers, and the sources contain no named buyer, no pricing, no customer, and no market evidence of any kind. A capability can be well-built and still lack a purchaser.

**What the state of the repository suggests about stage.** Governance, evidence, and custody are substantially complete and tested. The layer that feeds them real-world data is contract-defined but unconnected. That ordering is deliberate and defensible, and it also means the system has not yet been exercised against real inputs.

---

## 9. VALIDATION REQUIRED

What a competent evaluator would have to establish before relying on anything above. None of these is answered by the source material.

**Operational reality**
`UNKNOWN` Has the system ever run against real production data, from any source, at any volume?
`UNKNOWN` Is any instance deployed today, and who operates it?
`UNKNOWN` What throughput has been observed, and what happens under load beyond the one flaky latency test?

**Evidentiary strength**
`UNKNOWN` Has any auditor, examiner, regulator, or outside counsel reviewed the ledger output and stated whether it satisfies them? This is the load-bearing question for the entire premise and the sources are silent on it.
`UNKNOWN` Has the tamper-detection path been tested against a deliberate adversary rather than a test fixture?
`UNKNOWN` Has the twin's custody separation been reviewed by anyone outside the project?

**Compliance credibility**
`UNKNOWN` Has the CFPB Reg B lens been validated against real regulatory expectations by a qualified compliance professional?
`UNKNOWN` Are the four-fifths-rule implementation and the BISG demographic estimation statistically defensible in an examination?
`FACT` One disclosed design-level gap remains open by the sources' own account: variable renaming defeats two screens. `VERIFIED` (corrected in v3) A second item, that decision-time free-text reasoning is never captured, was found false on verification — a `reason` field is populated on every decision, though only with the deciding AI's own self-report.

**Organizational**
`UNKNOWN` Is there any team beyond the single documented individual, and any change control that does not depend on him?
`UNKNOWN` Who is the intended buyer, and has any prospective buyer expressed interest?
`UNKNOWN` What is the intended commercial or licensing model?

**Engineering**
`UNKNOWN` No static type checking is enforced. No API specification exists. Several structural questions the sources raise about their own subject remain unresolved — including which of two copies of one governance module is live, and whether two directories are duplicates or links.

---

## 10. UNKNOWN REGISTER

Recorded so that no reader mistakes silence for absence of a problem, or for evidence of one.

| Question an executive will ask | Status |
|---|---|
| What is the stated mission of this system? | `UNKNOWN` — no charter statement exists in any source |
| Is it in production anywhere? | `UNKNOWN` — the repository is described as production-ready; no deployment is documented |
| Who uses it? | `UNKNOWN` |
| What does it cost to run? | `UNKNOWN` |
| How does it perform at scale? | `UNKNOWN` |
| Who competes with it? | `UNKNOWN` — no competitive information appears in any source |
| How large is the opportunity? | `UNKNOWN` — no market information appears in any source |
| Is there a roadmap? | `FACT` Partially — some items are recorded as deferred, on hold, or cancelled, but no forward plan is stated |
| Is there a user interface? | `UNKNOWN` — only HTTP endpoints and command-line tools are documented |
| Has anyone outside the project assessed it? | `UNKNOWN` |

---

**End of Document 1.**

`FACT` This document is grounded solely in the four source documents named in the header, at repository state `68cadfb`. Statements marked `INTERPRETATION` are offered as readings, not findings, and are labeled individually throughout.
