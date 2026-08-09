# Mortgage Regulatory Lens Framework
## First-Pass Concept Design and Precedent Record

**Version:** v1
**Date:** 2026-08-07
**Status:** CONCEPT. No code written. No decisions locked by Wm at time of authoring.
**Scope:** Establishes the authoring framework for regulatory cassette lens content, using mortgage as the first vertical. Every subsequent regulatory lens is expected to inherit this framework, which is the reason for the level of detail below.
**Baseline:** sentinel_os origin/main at 44d26df (per the Aug 7 roadmap walkthrough). Nothing in this document has been verified against a live clone during this session.

---

## 0. Reader's orientation

This document records **why** a set of structural choices were made, not only what they are. It is written on the assumption that a future reader (human or model) will be authoring the second, fifth, or twentieth regulatory lens and will need to know whether a precedent set here was reasoned or arbitrary, so they can tell which parts are safe to depart from.

Three things this document is **not**:

- It is not the lens itself. No rule content is authored here.
- It is not a research report. The regulatory findings referenced are illustrative of structure, not exhaustive.
- It is not a locked decision record. Section 12 lists what still requires Wm's call.

A note on epistemic status, since the system this serves is built around exactly this discipline: this document contains **reasoning**, **findings**, and **assumptions**, and they are marked where the distinction matters. Findings drawn from secondary sources (law firm commentary, Justia, FindLaw) are flagged as such. None of the regulatory findings here have been verified against primary agency or legislative text.

---

## 1. The question this document answers

Wm's stated goal: a production-ready, not MVP, Sentinel OS for mortgages, prepared for any auditor's question no matter how vague or opaque.

That goal produced a concrete structural question, which is the one this document resolves:

> Should mortgage regulation be encoded as one cassette covering all of mortgage lending, or as several cassettes split by issuing authority (Ohio, Indiana, federal)?

And a follow-on question:

> Given the answer, what is the smallest vertical slice that can be built first without setting bad precedent?

---

## 2. Constraints inherited from sentinel_os

These were not invented for this document. They are pre-existing commitments in the system, and they are what make most of the choices below forced rather than free. Listing them explicitly matters because a future lens author needs to know which constraints are load-bearing.

**C1. The adoption model is agency-scoped.** Wm's stated model (July 22): an auditor from an agency walks into a regulated company and plugs in that agency's cassette; each agency publishes one official cassette all its auditors use. This is one lens per issuing authority, stated as a product model before any of this design existed.

**C2. Cassettes are content-hash bound.** A change to a cassette's content changes its hash and forces a version bump. Binding enforcement is live (closed July 22). This makes the granularity of a cassette a question about **change blast radius**, not just about organization.

**C3. Cross-lens conflict resolution already exists.** `resolve_tier_conflict` is wired into `RegulatoryDeck.judge()`/`explain()` (branch cross-lens-tier-conflict-wiring, commit 2ab81e3, rebuilt and pushed July 24-25). A deck holds multiple lenses and detects disagreement between them. Machinery for multi-lens operation is built and shipped.

**C4. The Provenance Rule.** Locked, Wm's own final wording: *"Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."* This governs lens content directly, not only outcome records.

**C5. Never a fabricated, fake, or guessed value.** Restated Aug 7. `estimated` is permitted as a stamp so long as it is labeled and never interchangeable with `verified` or `attested`.

**C6. Ambiguity is surfaced, never resolved by the system.** Wm: *"I never want Sentinel to make a decision with ambiguity."* The interpretation framework exists to hand ambiguity to humans. Regulation cassettes are specified to hold regs **plus explicitly marked opaque zones**.

**C7. Interpretation versions bind to a regulation's activation date.** Stated in the interpretation framework design. Retroactive re-testing is mandatory when a new reg has an activation date.

**C8. The COBOL principle.** Wm's 15-year framing: *"I want Sentinel to be so basic that no matter how powerful the model becomes, the COBOL concept keeps it in check."* A list-membership test does not get easier to defeat as models improve, because it never asks the model anything. It asks whether a past fact was on a list.

**C9. The observer posture.** Sentinel is a judge/witness, not an actor. Its core use case, in Wm's words (July 22): an auditor asks *"can you tell me why AI made a decision to decline this application."*

**C10. The recurring defect shape.** Identified in the Aug 7 walkthrough: real, fully-tested code that no entrypoint ever constructs. Options A, B, C and D on that roadmap were all instances of it. Any new build must have a runtime consumer or it becomes a fifth instance.

---

## 3. Decision 1: Separate lenses per issuing authority

**DECIDED (recommendation): one lens per issuing authority, loaded together in a `RegulatoryDeck`. Not a single mortgage lens.**

### 3.1 Options considered

**Option 1 — One mortgage lens.** All mortgage regulation, federal and state, in a single cassette. Simplest to author. Single artifact to load.

**Option 2 — One lens per issuing authority.** CFPB, HUD, NCUA, FHFA, Ohio DFI, Indiana DFI each get their own lens. Deck composition at load time.

**Option 3 — One lens per jurisdiction level.** Federal lens, Ohio lens, Indiana lens. Three artifacts. Coarser than Option 2, finer than Option 1.

**Option 4 — One lens per lifecycle stage.** Origination lens, servicing lens, default lens. Cuts across jurisdictions.

### 3.2 Why Option 2

**Reason 1: C1 already committed to it.** The adoption model is not "plug in the mortgage cassette." It is "the CFPB auditor plugs in the CFPB cassette." An Ohio DFI examiner loading a lens containing federal and Indiana rules is being shown obligations that examiner has no authority over. Option 2 is the only option where the artifact boundary matches the authority boundary that the product model already assumes.

**Reason 2: C2 makes blast radius a design variable.** Under Option 1, an Ohio administrative rule amendment (OAC 1301:8-7 is on a five-year review cycle) rehashes the entire mortgage lens, voiding the binding for an Indiana-only lender who is unaffected by the change. Under Option 2, that amendment invalidates the seal on the Ohio DFI lens and nothing else. This is not a convenience argument. Under a hash-binding trust model, an unnecessary version bump is a false positive tamper signal, and false positives in a tamper-evidence system erode the signal's meaning.

**Reason 3: Preemption is a relation between issuers, and Option 1 has no issuers.** The single most important structural fact about credit union mortgage regulation is that some state rules bind a state-chartered credit union and are displaced by NCUA authority for a federally-chartered one. That is not a property of a rule. It is a relationship between two issuing authorities on the same subject. In a single undifferentiated lens there is no "NCUA" and no "Ohio DFI" to hold a relationship between, so the fact becomes a free-text annotation instead of a structure the system can evaluate. Under C8, an annotation is not COBOL-simple; an edge between two named issuers is.

**Reason 4: C3 is already built and would be unused.** `resolve_tier_conflict` detects disagreement across lenses in a deck. Option 1 produces a deck of one and the resolver never fires. Building Option 1 means building against, rather than onto, machinery already shipped and tested.

**Reason 5: it is how the system already speaks.** `RegulatoryDeck` is plural by name and by construction. `CFPBRegBLens` is issuer-named and lens-named. The existing naming already encodes Option 2; Option 1 would require renaming or accepting a permanent mismatch between the vocabulary and the artifact.

### 3.3 Why not Options 3 and 4

**Option 3 (per jurisdiction level)** fails Reason 3 partially and Reason 1 fully. A single "federal" lens bundles CFPB, HUD, NCUA and FHFA, which have genuinely different authorities, different examination powers, and different relationships to a credit union. NCUA supervises the institution; the CFPB writes rules it does not examine a sub-$10B institution against. Collapsing those into one issuer destroys the distinction that answers "who is asking, and can they ask this?" Option 3 is the right **fallback** if per-issuer authoring proves too slow, and it degrades gracefully into Option 2 later because the issuer field is retained per rule regardless (see Section 5).

**Option 4 (per lifecycle stage)** is rejected because it makes the hash-binding problem worse rather than better: a CFPB servicing rule change and an Ohio foreclosure statute change would both rehash the same "default" lens, so the blast radius is now cross-jurisdictional as well as cross-issuer. It also has no correspondence to any real-world authority, so no agency could ever publish one, which contradicts C1 directly.

### 3.4 What this decision does **not** settle

It does not settle whether all six candidate issuers get built. Section 10 argues for starting with a slice that spans a small number of them. It also does not settle lens naming conventions, which is deferred (Section 12, Q4).

---

## 4. Decision 2: Three layers, not one

**DECIDED (recommendation): ontology, issuer lenses, and edges are three distinct layers with different provenance stamps.**

### 4.1 The problem this solves

Wm raised the possibility of encoding relationships that are "universal for mortgages, even if they're not named in the federal rules." That material is real and load-bearing. A mortgage has a lifecycle. Default has a meaning. A workout is a recognizable category. None of it is invented, and a lens that lacked it could not interpret the rules that assume it.

But it has no issuer.

Under C4 and C5, an unattributed assertion sitting inside a lens next to attributed ones is precisely the interchangeability the Provenance Rule forbids. An auditor's fair question about any lens assertion is "who said that, and where." For a universal-convention assertion inside an issuer lens, the honest answer is "nobody, it is how the industry works," and that answer, given from inside an artifact stamped as the CFPB's, is a governance failure regardless of how correct the assertion is.

### 4.2 The three layers

**Layer 1 — Ontology (universal, unattributed, stamped as convention).**
The mortgage domain vocabulary: lifecycle stages, what a default is, what resolution states exist, what a workout is, what a lien position means. This layer is **shared** across every issuer lens and referenced by them. It is stamped `convention`, a stamp that does not currently exist in the Provenance Rule's three-value set and is proposed here (Section 12, Q1).

This layer is also the correct home for **GSE, FHA, VA, and USDA overlays**, which are contract, not law. The Fannie/Freddie Servicing Guide is binding on a seller-servicer, but it binds by contract and no auditor from a government agency enforces it as regulation. Placing it in an issuer lens would misrepresent it as law. Placing it in ontology, stamped `convention`, states its real status.

This matters concretely for the mortgage cassette as already built. The locked design decision that a permanent loan modification always issues a new loan number is recorded in the cassette's own notes as a mortgage/GSE-reporting convention rather than a universal rule, and the July 29 secured-loan generalization note explicitly says it does not generalize. That is already a Layer 1 fact behaving correctly. This framework gives it a formal home.

**Layer 2 — Issuer lenses (attributed, citable, hash-bound, jurisdiction-scoped).**
Rules attributable to a named authority with a specific citation. Everything in this layer can answer "who said that" with a section number.

**Layer 3 — Edges (relationships between Layer 2 items).**
Preemption, displacement, floor-and-ceiling relationships, and substantive conflict. An edge is not a rule and does not belong inside either endpoint's lens, because an edge asserted from inside one issuer's artifact is that issuer claiming something about another's authority.

### 4.3 Why edges are their own layer rather than fields on rules

Considered and rejected: putting a `preempted_by` field on each rule inside the state lens.

Rejected because it makes the state lens assert a fact about federal authority. If Ohio's lens says "this rule is preempted by NCUA for federal charters," Ohio DFI is now publishing a claim about NCUA's reach, and the auditor's "who said that" question has a wrong answer: the citation is Ohio's, but the assertion is about NCUA. Preemption assertions have their own provenance (usually case law, agency interpretive letters, or 12 CFR 701.21 read against a state statute) and need their own citation field, which means they are records, not annotations.

The counter-argument, which is real: edges as a separate layer are harder to author and easy to leave incomplete, and an incomplete edge set is silently wrong in a way an incomplete rule set is not. Mitigation is proposed in Section 12, Q3.

---

## 5. Decision 3: The rule record schema

**DECIDED (recommendation): research output is authored as structured rule records, not prose, with the following fields.**

### 5.1 Why structured, and why this is the highest-leverage choice in the document

A prose regulatory report requires a human or model to extract each rule by hand into whatever the lens consumes. That extraction step is unaudited, unversioned, and is exactly where a guessed value would enter a system whose entire premise is that it does not guess (C5). If the research output is already the record format, extraction is a transcription with a diff, not an interpretation.

This is the single choice most likely to be inherited unchanged by every future lens, and therefore the one most worth getting right now.

### 5.2 Fields, with rationale

**`issuer`** — the authority. Forced by Decision 1. Retained even under a fallback to Option 3, so a coarse lens can be split later without re-authoring.

**`citation`** — exact section, not "state law." Wm's original research prompt already demanded this. It is the answer to "who said that."

**`activation_date`** — forced by C7. Interpretation versions bind to activation dates, so a rule without one cannot participate in the interpretation framework at all. Where a rule has been amended, the record needs both the current activation date and enough history to answer a question about a decision made under a prior version, because a governance ledger is queried retrospectively by definition.

**`entity_scope`** — credit union, CUSO, state charter, federal charter, servicer, and whether the rule reaches the institution or only a subsidiary. This field exists because the Ohio and Indiana findings both turned on it. Ohio's RMLA exempts depository institutions and pushes the requirement onto credit union service organizations, and Indiana's exemption structure at IC 24-4.4-1-202 does something structurally similar with enumerated carve-backs. A lens that recorded "Ohio requires X" without entity scope would be wrong for the majority of readers. *(Finding, secondary sources: McGlinchey Stafford commentary and Justia's rendering of R.C. 1322.04/1322.05 and IC 24-4.4-1-202. Not verified against primary text.)*

**`lifecycle_stage`** — origination, servicing, default, disposition. References Layer 1 ontology. Enables an auditor to scope a question to a stage, and enables the deck to skip rules for stages a decision does not touch.

**`trigger`** — the event that makes the rule apply. This is what makes a rule evaluable rather than merely stored. A rule with no expressible trigger is a strong signal it belongs in the not-checkable class (Section 6).

**`observable`** — the specific data Sentinel must already hold to evaluate the rule. This field, more than any other, is what converts research into buildable frame. It is also the field most likely to be filled in optimistically, so it should name actual fields where they exist (for example `decision.input_fields`, `resolved_value.resolution_type`) rather than describing data in the abstract.

**`checkability`** — see Section 6.

**`opacity`** — see Section 7.

**`conflict_edges`** — references to Layer 3 records.

**`provenance_stamp`** — `verified` (checked against primary agency or legislative text), `attested` (secondary legal source), or `estimated` (inferred, e.g. an effective date read from a bill's general effective-date provision rather than stated in the section). Required by C4. Every finding in the research conducted so far would currently stamp `attested` at best, since all of it came from secondary sources.

### 5.3 A note on the field that was considered and dropped

`severity` or `risk_weight` was considered and dropped. It reintroduces the exact problem flagged on July 23 when an "approve" binary on the combination matrix was renamed to escalate/review_priority: assigning severity to a regulation is compliance adjudication, not screening, and Sentinel does not adjudicate. If prioritization is needed it belongs to the customer's deployment configuration, not the lens content.

---

## 6. Decision 4: Checkability classification, and why not-checkable rules are retained

**DECIDED (recommendation): every rule is classified into one of three checkability classes, and none of the three is discarded.**

### 6.1 The classes

**`hard_checkable`** — evaluable from data Sentinel already holds at decision time. Example shape: whether an adverse action record carries a reason, and whether that reason references an input actually present on the decision.

**`attestable_only`** — the obligation is real and the lender must satisfy it, but Sentinel cannot observe compliance; it can only record that the lender asserts compliance, with the assertion's own provenance. Example shape: whether a required notice was actually mailed.

**`outside_observation`** — the rule is real, cited, and correctly held in the lens, but Sentinel has no path to it at all. Example shape: the Ohio sheriff's sale two-thirds minimum bid, where nothing in Sentinel's data model touches a sale.

### 6.2 Why the third class is retained rather than dropped

This follows directly from Wm's stated goal of being prepared for any auditor's question, no matter how vague or opaque.

If an auditor asks about a rule Sentinel does not hold, the system is silent, and **silence is indistinguishable from ignorance**. If instead the lens holds the rule stamped `outside_observation` with a recorded reason, the answer becomes: this rule is known, here is its citation, here is precisely why it falls outside what this system observes, and here is what would have to change for it to fall inside.

That is the same discipline already applied to unresolved outcomes. Wm's July 28 framing was that an auditor gets fed up with vague "we made a guess" answers, and that Sentinel's own INDETERMINATE state cannot be a mushy catch-all or it earns the same reaction. The fix there was a typed reason code plus a timestamp plus what would close it. This is that fix applied to regulatory coverage rather than to outcome resolution.

**Negative space is part of the frame.** A lens that contains only the checkable rules looks complete and is not, and cannot tell you which it is.

### 6.3 The cost, stated honestly

This roughly triples the research volume, because it means researching rules that will never fire a check. That is a real cost and it is the main argument against the exhaustive research scope. The counter is that classification is cheap once the rule is found, and the alternative is discovering the gap during an audit rather than during authoring.

---

## 7. Decision 5: Opacity marking and the interpretation handoff

**DECIDED (recommendation): opacity is a first-class field on the rule record, and marked opacity is the input to the interpretation framework's scenario zones.**

C6 requires that regulation cassettes hold regs plus explicitly marked opaque zones. The interpretation framework is built (commit 8f6d5f4, resolver seam wired via PR #8) and has no content. Marked opacity is the seam between them.

**The authoring rule proposed:** opacity is marked where the regulation's own text admits more than one defensible reading that would produce different system behavior. It is **not** marked merely because a rule is complex, and it is **not** marked where the ambiguity is about facts rather than about the rule.

The discipline matters because the interpretation framework's cost is human: legal reviews and approves every scenario, and monthly drift checks run against approved answers. Over-marking opacity converts that from a governance asset into a standing tax on the legal team, and a tax nobody pays becomes a process that quietly stops running.

**Worked example of correct marking**, from the research already conducted: the CFPB's position that a lender cannot satisfy adverse-action requirements by relying on the sample reason checklist when a model's reasoning does not map to it, and cannot excuse imprecision by model complexity. What counts as a sufficiently specific principal reason is genuinely contested, admits more than one defensible reading, and different readings produce different system behavior on the same decision. That is a real opaque zone. *(Finding, secondary sources. The current status of Circulars 2022-03 and 2023-03, including whether either has been rescinded or superseded as of August 2026, was NOT confirmed and is a gap.)*

**Counter-example of incorrect marking:** Indiana's 30-day settlement conference request window. The number is 30. It is not opaque. It may be *hard* to evaluate, which is a checkability question, not an opacity question.

---

## 8. Decision 6: Edge records

**DECIDED (recommendation): edges carry their own citation and their own provenance stamp.**

An edge asserts a relationship, and the assertion has a source distinct from either endpoint. A preemption edge between an Ohio rule and NCUA authority is grounded in something (12 CFR 701.21, an NCUA legal opinion letter, case law), and that grounding is the edge's citation.

Edge types proposed, minimal set:

- **`displaces`** — one issuer's rule removes the other's applicability for a defined entity scope. The state/federal charter distinction is the canonical case.
- **`floor`** — both apply; one sets a minimum the other exceeds. The likely shape of state civil rights statutes relative to ECOA and the FHA.
- **`conflicts`** — both apply and cannot both be satisfied. Expected to be rare and to be the highest-value finding when it occurs, because this is what `resolve_tier_conflict` exists to surface.

Deliberately excluded from the minimal set: `clarifies`, `implements`, `supersedes_within_issuer`. All three are real relationships, but the first two are commentary rather than governance-relevant, and the third is version history, which `activation_date` already handles.

---

## 9. Precedent summary

For the author of the second lens, the inheritable rules from the above:

1. One lens per issuing authority. If in doubt about whether two authorities are one, ask whether they could examine independently.
2. Anything without an issuer goes to ontology, stamped as convention, never into a lens.
3. Contract obligations (GSE, investor, insurer) are ontology, not lenses, regardless of how binding they are in practice.
4. Every rule record carries issuer, citation, activation date, entity scope, lifecycle stage, trigger, observable, checkability, opacity, edges, and provenance stamp.
5. Rules that cannot be checked are still recorded, classified, and given a reason. Coverage gaps are documented, not omitted.
6. Opacity is marked only where the text admits multiple defensible readings that change system behavior.
7. Edges live outside both endpoints and carry their own citation.
8. No rule enters a lens stamped `verified` without a check against primary text.

---

## 10. Pilot selection: the first vertical slice

### 10.1 Selection criteria

The first slice sets precedent, so it should exercise the awkward parts of the framework rather than the easy parts. Criteria applied, in priority order:

1. **Has a live runtime consumer.** Required by C10. Building a lens nothing constructs would make this the fifth instance of the recurring defect.
2. **Spans more than one issuer.** Edges are the least-tested part of the framework and the part most likely to be wrong. A single-issuer pilot would leave Decision 6 unexercised.
3. **Contains at least one genuine opaque zone.** Otherwise the interpretation seam stays untested.
4. **Contains at least one `outside_observation` rule.** Otherwise the hardest classification judgment goes unexercised.
5. **Operates on data Sentinel actually holds.** Not on data it would need a new ingestion path to obtain.
6. **Small enough to finish and verify.**

### 10.2 Candidates evaluated

**Candidate A — Adverse action on a declined mortgage application.**
Issuers: CFPB (Reg B), HUD/FHA, Ohio (R.C. Ch. 4112), Indiana (IC 22-9.5).

**Candidate B — The pre-foreclosure gate and first legal filing.**
Issuers: CFPB (Reg X), Ohio, Indiana.

**Candidate C — Loss mitigation evaluation sequencing.**
Issuers: CFPB (Reg X), Indiana (settlement conference), GSE overlays.

**Candidate D — Automated valuation model quality control.**
Issuers: NCUA and the interagency AVM rule.

### 10.3 Evaluation

| Criterion | A (adverse action) | B (foreclosure gate) | C (loss mit) | D (AVM) |
|---|---|---|---|---|
| Live runtime consumer | **Yes.** PR #20 ships adverse-action reason specificity in the live worker | No servicing event stream exists | No | No |
| Multi-issuer | **Yes, four** | Yes, three | Yes, plus contract | No, effectively one |
| Genuine opacity | **Yes, high** | Low, mostly deadlines | Moderate | Moderate |
| Has `outside_observation` rules | Yes | **Yes, many** | Yes | Yes |
| Data already held | **Yes.** Decision, reason, input fields | No | No | No, no valuation data model |
| Scope | Small | Medium | Large | Small |

### 10.4 Decision

**RECOMMENDED PILOT: Candidate A, adverse action on a declined mortgage application, across four issuers.**

Reasons, in order of weight:

**It is the only candidate with a live runtime consumer.** As of PR #20 (commit 6357f28), `sentinel_worker.py`'s main entrypoint constructs a `RegulatoryDeck` with the CFPB/ECOA/Reg B lens in LIVE mode, scoped to declared-proxy input screening and adverse-action reason specificity, flag-only. That is a real, shipped, running surface. Every other candidate would require building an ingestion path first, and would therefore repeat the exact defect shape identified across roadmap options A through D.

**It is Wm's own stated core use case, verbatim.** July 22: an auditor asks *"can you tell me why AI made a decision to decline this application"* and Sentinel is the system that can answer that. If the first regulatory lens is going to prove anything, this is the thing to prove.

**It is where the federal AI hooks actually live.** The CFPB circulars on adverse action and complex models are the most directly applicable federal position on algorithmic credit decisioning, and they attach to this exact decision point. Given that the state-level research surfaced no Ohio or Indiana AI-specific lending statute, this is likely to remain the strongest AI-relevant material in the entire mortgage corpus.

**It exercises Layer 3 without requiring rare conflicts.** Federal ECOA and FHA against Ohio R.C. Ch. 4112 and Indiana IC 22-9.5 is a probable `floor` relationship, which is the most common edge type and the right one to build the machinery against first.

**Its data is the data the cassette already handles.** `decision.input_fields`, the adverse-action reason, and the mortgage cassette's own `judge()` integrity scoring, which already penalizes a denial recorded without a documented reason, all sit at this decision point.

### 10.5 What is deliberately excluded from the pilot

Recorded so the exclusions are choices rather than oversights:

- **All servicing, default and disposition rules.** Out of lifecycle scope. Includes the entire Indiana settlement conference regime and the Ohio junior-lien notice statute, both of which were researched and are the more interesting state findings, and both of which should be the second slice.
- **Licensing and chartering.** Real and important, but it governs whether the institution may operate, not whether a given decision was sound. It is not a decision-point rule and does not fit the trigger/observable model cleanly.
- **HMDA and data reporting.** Institution-level periodic obligation, not per-decision.
- **NCUA institution-level rules.** Deferred with the preemption boundary, which the pilot touches only lightly since state civil rights statutes are a weaker preemption case than state lending statutes.
- **All Layer 1 ontology beyond the minimum the pilot needs.** Build the vocabulary the pilot consumes, not the whole domain model.

### 10.6 Success criterion for the pilot

The pilot succeeds if, at the end, an auditor question of the form *"why was this application declined, and does that reason satisfy your obligations"* can be answered with a citation, an issuer, a provenance stamp, and an honest statement of what the system could not observe. It fails if the answer is fluent but unattributed, which is the failure mode this entire framework exists to prevent.

---

## 11. Sequence after the pilot

Proposed, not decided:

1. Adverse action (pilot) — origination, four issuers.
2. Default and pre-foreclosure — servicing and default stages, three issuers. This is where the state divergence is sharpest and where the `displaces` edge type gets its first real test.
3. NCUA institution lens and the preemption boundary — builds the `displaces` machinery properly.
4. Ontology completion, including the GSE and investor overlays.
5. Remaining federal (Reg Z, Reg C, flood, appraisal), by issuer.

Rationale for putting default second rather than first despite it being the richer material: it needs an ingestion path that does not exist, and building that path is a separate decision with its own scope.

---

## 12. Open questions requiring Wm's decision

**Q1. Does the Provenance Rule gain a fourth stamp?** Layer 1 ontology needs a stamp meaning "true, universal, not attributable to an authority." `convention` is proposed. The alternative is `attested` with the industry as attestor, which is weaker because it implies a specific attestor who does not exist. This changes the wording of a rule Wm locked personally, which is why it is a question rather than a recommendation.

**Q2. Does a lens ever refuse to load without its edges?** An incomplete edge set is silently wrong. Options: (a) edges optional, gaps invisible; (b) edges optional but a deck reports its edge coverage; (c) a lens declares which other issuers it expects edges to, and the deck refuses to run LIVE if a declared edge is missing. Option (c) is the fail-closed choice consistent with the system's existing posture, and it is also the most likely to block work.

**Q3. Who authors, and what is the review gate?** This framework says nothing about whether a model may author rule records directly. Given C5 and given that the interpretation framework already requires legal approval for AI-generated scenarios, the parallel would be that AI may author records but only `attested` and `estimated` stamps, and `verified` requires a human check against primary text. Proposed, not decided.

**Q4. Lens naming and versioning convention.** `CFPBRegBLens` exists. Whether an Ohio lens is `OhioDFILens`, `OHRMLALens`, or something else determines whether lens names track issuers or track statutes, and the two diverge when an issuer administers several statutes.

**Q5. Is the fallback to Option 3 acceptable if per-issuer authoring proves too slow?** Section 3.3 argues the fallback degrades gracefully because the issuer field is retained per rule. Confirming that the fallback is acceptable in advance would let authoring start without resolving issuer granularity perfectly.

---

## 13. Provenance of this document

Per C4, applied to this document itself.

**Reasoning** (Sections 3 through 9): authored in this session, grounded in the constraints listed in Section 2. Those constraints are drawn from prior sessions and are recorded as Wm's own stated positions. They have not been re-confirmed with him in this session.

**Regulatory findings** referenced anywhere above: all stamped `attested` at best. Sources were secondary (Justia, FindLaw, law firm commentary, a county law library guide). None were verified against primary agency or legislative text. Specific known gaps:

- The current status of CFPB Circulars 2022-03 and 2023-03 as of August 2026 was not confirmed.
- No Ohio or Indiana AI-specific lending statute or regulator guidance was found, but absence was not affirmatively confirmed, only unobserved in a first-pass search.
- Whether Ohio or Indiana has a community reinvestment equivalent reaching credit unions was not established.
- Indiana's post-judgment waiting period before sale was believed but not verified.

**Code and repository claims** (PR numbers, commit hashes, what is wired where): drawn from prior session records, not from a live clone in this session. PR #20's scope in particular should be re-verified against the repo before the pilot is built on top of it.

**Nothing in this document has been locked by Wm.** It is a recommendation set awaiting decision.

---

*End v1.*
