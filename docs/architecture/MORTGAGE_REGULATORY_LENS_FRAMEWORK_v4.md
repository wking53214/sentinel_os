# Mortgage Regulatory Lens Framework
## Concept Design and Precedent Record, v4

**Version:** v4, supersedes v3 (2026-08-08). Second same-day supersession.
**Date:** 2026-08-08
**Status:** Architecture LOCKED where marked. Five new locks in this version. One rule record drafted (Section 11), still authoring-incomplete. Zero code written.
**Scope:** Establishes the authoring framework for regulatory cassette lens content, using mortgage as the first vertical. Every subsequent regulatory lens inherits this framework, which is the reason for the level of detail below.
**Baseline:** sentinel_os origin/main at 44d26df (per the Aug 7 roadmap walkthrough). Nothing in this document has been verified against a live clone.
**Continuity note:** this document is the sole artifact a new chat session needs to pick this work up without re-deriving anything below. If you are that session: read this whole file before authoring any rule record. Do not rely on v3, which now misstates five decisions including Q2.

---

## 0. Changelog from v3

**Five locks landed. Four of them change something v3 presents as open or settled differently.**

1. **Section 5.1 record granularity is LOCKED at option A**, one obligation per record. v3 had it as a recommendation explicitly flagged as unconfirmed.
2. **Q8 record identity is LOCKED at "B-prime."** Records get a stable assigned address; edges reference that address plus the endpoint content hash they were evaluated against. v3 had no answer at all.
3. **Edge temporal semantics is LOCKED at the overlap rule with authored narrowing.** This question does not appear anywhere in v3. It was surfaced by an outside cross-check.
4. **Q2 edge completeness is REFINED AND LOCKED.** The required edge set is now computed from deck membership, not declared by a lens author. This supersedes the Q2 wording in v3 Section 3, which a reader following v3 alone would implement incorrectly.
5. **Section 8 item 1 (the Q2 bootstrapping trap) is CLOSED** as a consequence of 4.

**Structural changes to the document:**

- New Section 8 collects the whole Layer 3 edge model, which was previously scattered. Sections 8 through 13 of v3 are renumbered as 9 through 15. v3's Section 8 (open items) is now Section 9; v3's Section 9 (pilot) is now Section 10; v3's Section 10 (first record) is now Section 11.
- New Section 14 documents the three-arbiter cross-check method, which has now been run twice and is a repeatable practice rather than a one-off.
- Section 5 gains a record `address` field. Every record drafted before this version needs one retrofitted, Section 11 included.

**Open items went from four to seven.** Two closed (items 1 and 4), five opened. This is expected and not a regression: each lock exposes the next layer of question. They are smaller than the ones that closed.

**Nothing in the pilot scope changed.** Same event, same four issuers.

---

## 1. The question this document answers

Wm's stated goal: a production-ready, not MVP, Sentinel OS for mortgages, prepared for any auditor's question no matter how vague or opaque.

That goal produced a concrete structural question:

> Should mortgage regulation be encoded as one cassette covering all of mortgage lending, several cassettes split by issuing authority, or something finer still?

And four follow-ons:

> Given the answer, what is the smallest vertical slice that can be built first without setting bad precedent? **(Answered: adverse action, four issuers. Section 10.)**
> How do future lens authors extend this without re-deriving it from scratch? **(This document.)**
> What does an actual rule record look like when the schema meets real regulatory text? **(Section 11, and it changed the schema. Section 5.1.)**
> How does a relationship between two issuers' obligations stay honest over time? **(New in v4. Section 8.)**

---

## 2. Constraints inherited from sentinel_os

Unchanged from v1 through v3. Restated because a future reader needs to know which constraints are load-bearing and where they came from.

**C1. The adoption model is agency-scoped.** Wm's stated model (July 22): an auditor from an agency walks into a regulated company and plugs in that agency's cassette; each agency publishes one official cassette all its auditors use.

**C2. Cassettes are content-hash bound.** A change to a cassette's content changes its hash and forces a version bump. Binding enforcement is live (closed July 22).

**C3. Cross-lens conflict resolution already exists.** `resolve_tier_conflict` is wired into `RegulatoryDeck.judge()` and `explain()` (commit 2ab81e3). A deck holds multiple lenses and detects disagreement between them.

**C4. The Provenance Rule.** Wm's own final wording: *"Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."* Now four stamps, per Q1.

**C5. Never a fabricated, fake, or guessed value.** `estimated` is permitted so long as it is labeled and never interchangeable with `verified` or `attested`.

**C6. Ambiguity is surfaced, never resolved by the system.** Wm: *"I never want Sentinel to make a decision with ambiguity."* Regulation cassettes hold regs plus explicitly marked opaque zones.

**C7. Interpretation versions bind to a regulation's activation date.** Retroactive re-testing is mandatory when a new reg has an activation date.

**C8. The COBOL principle.** Wm's 15-year framing: a list-membership test does not get easier to defeat as models improve, because it never asks the model anything. It asks whether a past fact was on a list.

**C9. The observer posture.** Sentinel is a judge and witness, not an actor. Core use case (July 22): an auditor asks *"can you tell me why AI made a decision to decline this application."*

**C10. The recurring defect shape.** Real, fully-tested code that no entrypoint ever constructs. Any new build must have a runtime consumer or it becomes another instance.

---

## 3. Locked answers, index

**Q1. Does the Provenance Rule gain a fourth stamp? LOCKED: yes.** `convention` added alongside verified, attested, and estimated, for Layer 1 ontology: material that is true and universal but has no issuing authority. This changes the wording of a rule Wm locked personally. A guard on this stamp is recommended but not yet confirmed; see Section 9, item 1.

**Q2. Does a deck ever refuse to run without its edges? LOCKED, and REFINED in v4.** Yes, fail-closed. The refinement is in what defines the required set. Full statement in Section 8.3. **The v3 wording of this answer is superseded and should not be implemented.**

**Q3. Who authors, and what is the review gate? LOCKED.** A model may author rule records at `attested` and `estimated` stamps only. `verified` requires a human check against primary agency or legislative text. Mirrors the interpretation framework's legal-approval gate.

**Q4. Lens naming and versioning. RESOLVED by the boundary decision itself.** Pattern: `<Issuer><Instrument>Lens`. Examples: `CFPBRegBLens` (pre-existing), `OhioCivilRightsLens`, `IndianaFairHousingLens`.

**Q5. Is a coarser fallback acceptable if per-issuer authoring proves too slow?** Wm: resolving issuer granularity "perfectly" is an oxymoron, since nothing can be perfect. Read as: proceed, do not block on perfecting granularity.

**Q6. Does LIVE mode require `verified`, or is `attested` sufficient if disclosed? LOCKED: verified, across the board.**

No lens content runs LIVE, in any posture including flag-only, until a human has checked it against primary agency or legislative text. A flag-only carve-out for `attested` content was proposed in v2, considered, and explicitly rejected by Wm as inconsistent with his own build philosophy. The rejection matches a repeated pattern this build: catching and reversing gaps where a system technically discloses or technically returns a status but does not actually verify (the F4 BISG geocoder silent-downgrade fix, the twin_migrate.py swallowed-401 fix).

The interim state before a lens clears verification is **not silence.** The OutcomeV1 discipline applies: a timestamped artifact stating that the lens is not yet running, what is blocking it, and what citations are in the queue to close it. This is C4 applied to the system's own readiness.

**Practical consequence, and it is significant:** the pilot as drafted cannot run. Everything a model authors tops out at `attested` under Q3, and Q6 requires `verified`. A human primary-text pass is on the critical path between authoring and any runtime behavior at all. This is a deliberate cost, accepted knowingly.

**Q7. Where does interpretive guidance live? STILL OPEN.** See Section 9, item 2.

**Q8. What identifies a rule record? LOCKED in v4.** Full statement in Section 5.2 and Section 8.1.

**Q9. Does a first-class source-section object join the model? OPEN, not yet opened as a work item.** See Section 9, item 3.

---

## 4. The lens boundary: LOCKED, with full decision history

The single most consequential structural decision in this document. Wm asked explicitly for the reasoning to be documented to the minutia, since every future lens inherits it. The full arc is recorded rather than only the conclusion, because the arc is itself part of the precedent: this decision was independently re-derived three times by three different reasoning processes before being locked.

### 4.1 v1's original position, and the contradiction that reopened it

v1 recommended one lens per issuing authority, full stop. The reasoning was sound as far as it went: it matched Wm's adoption model (C1), kept hash-binding blast radius scoped (C2), and gave `resolve_tier_conflict` (C3) real work.

The contradiction surfaced during the naming discussion. The already-shipped `CFPBRegBLens` is not scoped to the issuing authority. It is scoped to the issuing authority plus one specific regulation. Under a strict reading of v1's position, the CFPB should have exactly one lens covering Reg B, Reg X, Reg Z, Reg C, and Reg F together. That is not what exists, and building toward it would have meant reworking a live artifact to fit a document that had never been checked against it.

### 4.2 The fork

**Option A:** one lens per issuer. A regulator with six regulations gets one lens containing all six.
**Option B:** one lens per issuer plus instrument. A regulator with six regulations gets six lenses.

### 4.3 Why B

- **Blast radius.** Under A, an amendment to any one CFPB regulation rehashes the entire CFPB lens, voiding the binding for every lender whose use case never touched the amended regulation. This is the identical defect that killed the single-mortgage-lens option, recurring one level down. A false tamper signal under a hash-binding trust model is worse than no signal, because it teaches the audience to ignore version bumps.
- **Activation dates.** C7 binds interpretation versions to a regulation's activation date. A lens holding six regulations has six activation dates and no single honest answer to "when did this lens's content take effect."
- **It matches the code.** `CFPBRegBLens` already exists. B costs zero renaming.
- **It degrades gracefully.** An issuer with exactly one instrument produces an identical result under either option.

Honest cost of B, acknowledged at the time: more artifacts to author and load, plus an open question about jointly-issued rules, since B's original "one issuer per lens" framing did not obviously accommodate that case.

### 4.4 Three independent cross-checks, same conclusion

**Cross-check 1, the FRED exercise.** Reasoning from a hypothetical new regulatory agency twenty years out surfaced two things a mortgage-only framing could not: a young, fast-amending authority hits blast-radius damage in months rather than the decade a mature agency would take, which is a stronger argument for B than the original one; and the interagency AVM rule (co-issued by NCUA, OCC, the Fed, FDIC, CFPB, and FHFA) is a real present-day example of the joint-issuance case. This produced the requirement that **`issuer` be a set, not a scalar.**

This cross-check was later self-audited and found to be a generative exercise, not a verification one. It was run single-sided, and one supporting claim (that false tamper signals erode trust "within a year") was found to be plausible narrative with no grounding, and was demoted. The issuer-as-set finding survived, because it traced to a checkable fact rather than to narrative plausibility.

**Cross-check 2, an independent AI system given a neutral framing of the same fork.** Converged on B. Contributed two new points: edges between instrument-level lenses carry precise semantic meaning ("Instrument X overrides Instrument Y") while authority-level edges are ambiguous ("something in A relates to something in B"), which directly strengthens the C3 and Q2 edge machinery; and a formal mechanism for the "new authority does not know its taxonomy yet" problem. This is now the locked lineage mechanism, Section 4.6.

Genuine corroboration, but not a blind trial: the prompt described both sides and named joint issuance as decisive before the second system reasoned. It could have argued for A and did not, which is real signal, but the framing was not neutral in the strongest sense.

**Cross-check 3, a third opinion given the same joint-issuance test case.** Converged on B independently. Contributed a concrete mitigation menu for B's known cost. See Section 4.7.

### 4.5 Decision, final

**LOCKED: lens identity = issuing authority plus instrument. `issuer` is a set on the lens record, not a scalar.**

A lens may have one or many issuers. An authority may issue one or many instruments. Joint issuance is represented natively, one lens with multiple issuer tags, never as duplicated content and never as a special-case lens type.

### 4.6 The lineage and decomposition mechanism: LOCKED

A new authority may publish one provisional, bundled lens before its instrument taxonomy is stable. When the taxonomy stabilizes, the bundle is formally decomposed into separate instrument-scoped lenses through a structural migration, not an ordinary content edit. The original provisional lens remains historically addressable so a decision governed under it stays queryable; the new lenses receive their own independent seals.

This preserves the real insight underneath Option A (a young authority should not be forced into premature structure) without adopting Option A's blast-radius defect once the taxonomy is known.

### 4.7 The edge-burden mitigation menu: LOCKED, 2 of 3 adopted

The known cost of B is that edge burden scales with instrument count, which is larger than authority count.

**REJECTED: wildcard or group edges** (a lens declares one edge to `Authority:Regulator_Name:*`). Rejected because it directly defeats the fail-closed guarantee: a wildcard auto-satisfies the edge check for every instrument that authority ever publishes, including ones that do not exist yet, meaning the specific evaluation the check exists to force never happens. This reduces cost by weakening the protection, which is not a mitigation.

**This rejection has since done work twice more.** It is the same structural argument that eliminated floating edge endpoints in Section 8.1 (a temporal wildcard) and that forced the Section 8.3 refinement (an anticipation wildcard). Treat it as a general principle: **any mechanism that pre-satisfies a check for cases nobody has examined is defeating the check, regardless of which axis it operates on.**

**ADOPTED: the sandbox and lineage mechanism**, Section 4.6. Solves "the authority's own taxonomy is not yet stable."

**ADOPTED: deck-level atomic multi-lens updates.** Solves a distinct problem lineage does not cover: an authority with a stable taxonomy deliberately revising several of its own instruments together as one coherent policy act. The deck compiler validates the full multi-lens cluster in memory before committing, treating it as a single atomic change, so a coordinated revision never passes through an inconsistent intermediate state. Precedent already in the repo: the interpretation framework's anchoring design (Aug 6) commits to the same fail-closed discipline one layer up.

---

## 5. The rule record schema

Research output is authored **as** these records, not as prose, so that turning research into lens content is a transcription with a diff, not an unaudited interpretation step.

- **`address`**: **new in v4.** The record's stable identifier. See Section 5.2.
- **`label`**: **new in v4.** Human-readable name. Ordinary record content, correctable without breaking anything. See Section 5.2.
- **`issuer`**: a SET of issuing authorities. Issuer is the body that promulgated the instrument, not the body that enforces it against a given institution. See Section 11 for why this distinction is load-bearing.
- **`instrument`**: the specific regulation, statute, or rule this record belongs to.
- **`citation`**: exact section or sections. Never "state law" or "federal law" generically. Under 5.1 this is a set, since one obligation can require citing more than one paragraph.
- **`activation_date`**: required for C7. Where a rule has been amended, both the current activation date and enough history to answer a question about a decision made under a prior version.
- **`entity_scope`**: credit union, CUSO, state charter, federal charter, servicer, and whether the rule reaches the institution or only a subsidiary.
- **`lifecycle_stage`**: origination, servicing, default, disposition. References Layer 1 ontology.
- **`trigger`**: the event that makes the rule apply. A rule with no expressible trigger is a strong signal it belongs in the not-checkable class.
- **`observable`**: the specific data Sentinel must already hold to evaluate the rule. Should name actual fields where they exist, not describe data abstractly.
- **`checkability`**: `hard_checkable`, `attestable_only`, or `outside_observation`. See Section 6.
- **`opacity`**: whether the rule's text admits more than one defensible reading that would change system behavior. See Section 7.
- **`conflict_edges`**: references to Layer 3 edge records. Types: `displaces`, `floor`, `conflicts`, and negative. See Section 8.
- **`provenance_stamp`**: `verified`, `attested`, `estimated`, or `convention` (Layer 1 ontology only, per Q1).

Considered and dropped, still dropped: `severity` and `risk_weight`. Assigning severity to a regulation is compliance adjudication, which Sentinel does not do (the July 23 escalate and review_priority precedent).

### 5.1 Record granularity: LOCKED at option A

**One rule record equals one obligation, not one C.F.R. section.**

Drafting the first record (Section 11) exposed the problem. 12 C.F.R. 1002.9 carries at least four distinct obligations with materially different checkability:

| Obligation | Checkability |
|---|---|
| Specific reasons stated, non-generic | `hard_checkable` |
| 30-day notice timing | `outside_observation` |
| Notice content elements (creditor address, ECOA statement, agency name) | `outside_observation` |
| Right-to-request-reasons disclosure | `outside_observation` |

The options evaluated:

- **A. Record scope equals one obligation.** 1002.9 yields four or five records. Honest per-field values. More authoring volume. **CHOSEN.**
- **B. Record scope equals one C.F.R. section.** Fewer records, but `checkability` becomes untrue.
- **C. Record scope equals one section, with `checkability` as a set.** Schema change, and it breaks the trigger and observable pairing, since each obligation has different observables.

**Why A won.** Every field takes one true value. Provenance can be granular, which matters under Q6: a human can verify the one `hard_checkable` obligation and let it run, instead of holding it hostage to three siblings that can never usefully run anyway. Blast radius stays scoped, since amending the timing obligation does not rehash the specific-reasons obligation. Activation dates stay singular. Edge semantics stay precise.

**Why C was eliminated first (this document's own arbiter) and why both outside arbiters eliminated B first instead.** C fails because `checkability` as a set is not the same move as `issuer` as a set. Set membership works for `issuer` because the members are co-equal and all apply to the same content. Different checkability values applying to different parts of a record is a partition, not a membership, and a set throws away which value belongs to which part. Restore the assignment and each entry needs its own trigger and observable, which is option A inside a container, with two identity tiers instead of one. Both outside arbiters instead eliminated B first on the sharper ground that a scalar field cannot represent a heterogeneous unit at all, which the worked example proves in one move. Both routes reach the same place.

**The known weakness of A, and it is real.** Sections are externally enumerable; obligations are not. No authority publishes a list of the obligations inside a section, so two competent authors can decompose the same section differently, and a missed obligation is invisible in a way a missed section is not. All three arbiters found this independently and all three proposed a mitigation rather than reversing.

**Recommended mitigation, NOT yet confirmed:** every record names its parent section, and each section carries a decomposition attestation stating that obligation extraction was performed and by whom. That converts proof-by-enumeration into proof-by-attested-enumeration, one step weaker, at almost no new cost, since the attestation is a claim the Q6 human pass makes anyway. See Section 9, item 4.

**Cost priced during the decision, and it determined Q8.** Under A an obligation's citation is a set, not a scalar. Section 11 demonstrates it: the specific-reasons obligation cites 1002.9(a)(2)(i) for the duty and 1002.9(b)(2) for the standard, and the reverse also occurs, one paragraph carrying two obligations. So citation cannot be the identifier. A forces an assigned identifier rather than a derived one, which turns out to be the property that makes Q8 solvable at all.

### 5.2 Record identity: LOCKED

**Two parts, and separating them is the whole design.**

- **`address`.** Assigned once, never edited, never reused, deliberately NOT the content hash. It answers "which obligation." Uniqueness comes free from the locked lens boundary: an address is unique within its lens, and the global address is lens plus record. No new namespace, no collision authority.
- **`label`.** Human-readable, ordinary record content, correctable without touching the address. It answers "what is this called."

**Why they must be separate.** A readable identifier looks helpful right up until the name turns out to be wrong or the obligation is understood differently, at which point you either live with a misleading address or break every edge fixing it.

**Why the address is not the content hash.** Amendment changes content. A hash-based address would break every edge pointing at the record at the exact moment the edge most needs re-evaluation, and it would break the record's own historical addressability.

**Why the address is not derived from citation.** Citation moves on renumbering. A citation-derived address would not survive the event it exists to survive.

**Retirement, not deletion.** When a rewrite splits one obligation into two or merges two into one, the old address retires with a forward pointer to its successors. Same mechanism as the 4.6 lens lineage, one layer down. Addresses are never reused, so a decision governed under the old record stays queryable.

**One address scheme covers all three layers.** Ontology records and edge records get addresses too, since `conflict_edges` references edge records and edges therefore need endpoints of their own.

---

## 6. Checkability classes

**`hard_checkable`**: evaluable from data Sentinel already holds at decision time.
**`attestable_only`**: the obligation is real, but Sentinel can only record the lender's assertion of compliance, stamped with its own provenance.
**`outside_observation`**: the rule is real and cited, but nothing in Sentinel's data model touches it.

All three retained, none discarded. Silence is indistinguishable from ignorance. A rule stamped `outside_observation` with a stated reason is a defensible answer to an auditor; an absent rule is not, and the absence cannot be told apart from an oversight.

---

## 7. Opacity marking

Marked only where the regulation's own text admits more than one defensible reading that would produce different system behavior, never merely because a rule is complex. Over-marking converts the interpretation framework's legal-review gate from a governance asset into a standing tax the legal team will eventually stop paying.

---

## 8. The Layer 3 edge model: LOCKED, new in v4

An edge is a relationship between two obligations under different issuers. Types: `displaces`, `floor`, `conflicts`, and negative. An edge lives outside both endpoints, carries its own citation, and carries its own provenance stamp independent of the stamps on either endpoint. **That last property decided two of the three questions below and should not be forgotten.**

### 8.1 What an edge points at: LOCKED at "B-prime"

**An edge references each endpoint's stable address plus the content hash it was evaluated against.**

Any change to an endpoint breaks the edge, raises a typed error, and blocks LIVE until a human re-evaluates. The break carries a **typed staleness reason** describing what changed, and the model prepares the diff along with its own read of whether the change was cosmetic, for the human to confirm or overrule. The labor is mechanical; the judgment stays human.

**Options rejected:**

- **Address alone, floating.** The edge survives any endpoint change with no maintenance. Rejected: it is a temporal wildcard, auto-satisfying the completeness check across versions that do not exist yet, which is the Section 4.7 defect on a different axis. Worse, the edge keeps carrying a `verified` stamp earned against text no longer in force, which is a stamp asserting a check nobody performed. That violates C4 and C5 directly.
- **Address plus a declared list of the endpoint parts the relationship depends on.** The edge breaks only when a declared dependency changes. Rejected: it requires an author to predict at authoring time which future amendments could matter, and the dependency list's completeness cannot be proven. If a definition changes outside the declared set and indirectly changes the obligation's scope, the edge does not break and nobody is told.

**Held, not rejected: pinning to a normalized semantic projection** of the record rather than the full content hash, so cosmetic change leaves the pin intact. Both outside arbiters proposed this independently, twice. It is held rather than adopted because the projection must define which parts of a regulation carry legal meaning, which no authority publishes. That is a new interpretive layer with no external validation, which is the same objection that made 5.1 hard, and its failure mode is false validity: a projection that wrongly classifies something as cosmetic produces an edge that silently survives a change that mattered. **Adopt only if measured churn justifies it. It is a response to observed pain, not a preemptive design.**

**Churn is probably smaller than it looks.** Both outside arbiters modeled churn as if records were section-sized. Under 5.1 they are not. An obligation-scoped record has very little cosmetic surface, and a paragraph renumbering touches roughly one field.

### 8.2 When an edge applies: LOCKED at the overlap rule

Section 8.1 answers the **evidence** question: which exact texts a human checked. This answers the **applicability** question: during what period the relationship governs. They are different, and an edge can have impeccable evidence and still be applied to a decision it never governed.

**An edge's window is computed as the overlap of the effective windows of the two endpoint versions it was evaluated against.** It is never authored from scratch.

**One permitted deviation:** an author may narrow the window, never widen it, and a narrowing requires its own citation. Transition provisions are real, and a rule reaching only applications received after a stated date is a narrowing the endpoints' dates cannot express. Widening is never valid, because it would assert coverage over a period where one endpoint's evaluated version was not in force.

**Why derived rather than authored.** Every authored temporal claim is an interpretive judgment with no external validation. The overlap rule needs no judgment; it falls out of dates already on the records. Same reasoning that put the semantic projection on hold in 8.1.

**The defect this closed.** Records could already be version-selected for a historical decision through `activation_date`. Edges had no equivalent, so a deck answering an auditor's question about a 2024 decision would select 2024 records and apply a current edge to them. C7 mandates retroactive re-testing on activation-date changes and had no trigger at the edge layer at all.

**Consequence that improves 8.1 at no cost.** A break is not "this edge is wrong." It is "this edge's window closed and the successor window is unevaluated." The old edge stays addressable with its window intact and keeps governing the decisions it actually governed. **Auditing past decisions continues to work during a break; only new decisions block.** The system goes dark on the leading edge, not entirely. This is the real answer to the capacity and abandonment risk, and it costs nothing, because it describes what the overlap rule already produces.

**Edge versioning follows from it.** A break creates a **successor edge with its own address**, not a version bump on the existing one. Addresses are never reused, the old window stays queryable, and the lineage pointer runs forward. Same shape as record retirement in 5.2.

**Interim behavior needs no new mechanism.** The window between amendment and human re-evaluation is covered by 8.3 (refuses LIVE) plus Q6's OutcomeV1 artifact.

### 8.3 Edge completeness: LOCKED, deck-scoped

**Any two lenses loaded into the same deck must have an edge record between them, positive or negative, or the deck refuses to run LIVE.**

This supersedes the v3 statement of Q2, under which a lens author declared which peers it expected edges to.

**Why the declaration version failed.** No author can enumerate lenses that do not exist yet. Reg B declares edges to HUD, Ohio, and Indiana. A Michigan lens is published later and loaded into a deck alongside Reg B. Reg B never declared Michigan, so the check passes and the deck runs, with the CFPB and Michigan pair never evaluated by anyone. No error, no flag. That inverts the entire rationale for the check: a missing edge is supposed to mean nobody evaluated the pair, and here it means nobody anticipated the pair. It is the Section 4.7 wildcard defect a third time, on the anticipation axis.

**What deck-scoping fixes.**

- **The bootstrapping trap dissolves.** A one-lens deck has zero pairs, so the first lens ever built runs. The trap only ever bit under author declaration.
- **The bidirectional rider is moot.** A pair has no sides.
- **The raise-not-degrade rider is already satisfied** by 8.1's typed staleness error, which is the same mechanism.
- **Cost lands where the pair becomes relevant**, on whoever assembles a deck, at assembly time, rather than on an author guessing years ahead.

**Honest cost.** Pairs scale as n² in deck size. Four lenses is six pairs, ten lenses is forty-five. Bounded because decks are use-case scoped rather than global, but real.

**Negative edges.** "Evaluated, no relationship on this obligation, here is the basis" is structurally an ordinary edge: it has an address (5.2), a pinned evidence state (8.1), and a window (8.2). It therefore goes stale exactly like a positive edge when an endpoint amends, which is what keeps it from being a permanent free pass. **Under deck-scoping, negative edges become the majority artifact in the system rather than the exception,** since most pairs in most decks will have no relationship.

**Recommended and NOT yet confirmed:** a negative edge's basis must state **what was searched**, not only what was concluded. "Evaluated against the whole of R.C. Ch. 4112 as of [date]" is falsifiable. "No relationship found" is not. This matters because verifying an absence is more expensive than verifying a presence, which is the opposite of the intuition, and because without it the majority artifact in the system is one nobody can audit. See Section 9, item 5.

---

## 9. Open items: NOT locked

A future session must not assume any of these are settled. Two of v3's four items are closed. Five are new.

**Item 1. Q1's guard.** Carried from v3 unchanged and still the only original item untouched. `convention` should not function as a confidence level below `estimated`. It is orthogonal: it means "no issuing authority exists for this," not "we are less sure." Proposed guard, not confirmed: `convention` is valid only in Layer 1 ontology, and still requires a stated basis even without a citable issuer. Not blocking for Layer 2 records; becomes blocking the moment the first ontology record is authored, which is imminent, because `lifecycle_stage` is an ontology reference. **Smallest and most independent of the seven.**

**Item 2. Q7, interpretive guidance has no home.** Carried from v3. The CFPB circulars on adverse action for complex models are the most relevant AI-in-lending material in the corpus, and they are the natural resolver for Section 11's opacity zone. But a circular is not a regulation. Under the issuer-plus-instrument lock it is either its own lens (which feels wrong for non-binding guidance), or opacity-resolution material carried inside the Reg B lens, or a new record class. The current edge type set has no type meaning "same issuer explains its own instrument," and `clarifies` was deliberately excluded in v1. Now unblocked by 5.2, since a pointer at a named opacity zone has something to point at.

**Item 3. Q9, the source-section object.** Both outside arbiters independently proposed adding a first-class source-section record and treating record-to-paragraph as its own many-to-many mapping, separating what the authority published from what Sentinel determined it requires from which passages support that determination. It is a genuinely strong idea and it is a new record class, so it was not adopted by momentum inside the granularity decision. It overlaps substantially with item 4, and the two should probably be decided together.

**Item 4. The parent-section pointer and decomposition attestation.** The recommended mitigation for 5.1's known weakness, described in 5.1, not confirmed. The lighter-weight cousin of item 3.

**Item 5. Negative-edge search scope.** Described in 8.3, recommended, not confirmed.

**Item 6. Does an amended record drop its own `verified` stamp?** Nowhere stated in any version of this document. If it does not, 8.1 catches stale edges while stale records run LIVE unchallenged, which is a larger hole than the one 8.1 closes. **Notable methodologically: this gap was deliberately withheld from both outside cross-check prompts and neither arbiter found it independently across two passes.**

**Item 7. Corrections to a closed window.** Surfaced by 8.2. Re-evaluation after a break can find either that the relationship changed going forward, which the successor edge handles cleanly, or that it was **also wrong for the window that just closed**, meaning past decisions were governed by an incorrect edge. The framework cannot currently distinguish a correction to a closed window from the opening of a new one. Audit-relevant, since only one of those two implies past answers need revisiting.

**Closed since v3:**
- The Q2 bootstrapping trap (v3 item 1), dissolved by 8.3.
- Q8 record identity (v3 item 4), locked at 5.2. **Every record drafted before v4 needs an address retrofitted, Section 11 included.**

---

## 10. Pilot: CONFIRMED

**Adverse action on a declined mortgage application, across four issuers.**

- **CFPB**: Regulation B (adverse action notice content and timing, 12 C.F.R. 1002.9), plus the CFPB's circulars on adverse action where a credit decision relies on a complex or opaque model. The circulars' structural home is unresolved; see Section 9, item 2.
- **HUD**: Fair Housing Act, 24 C.F.R. Part 100.
- **Ohio**: R.C. Ch. 4112, administered by the **Ohio Civil Rights Commission**. Not the Ohio Division of Financial Institutions. OCRC and the Ohio DFI are separately examining authorities and would be separate lenses regardless.
- **Indiana**: IC 22-9.5, administered by the **Indiana Civil Rights Commission**. Same distinction.

### 10.1 Why this pilot

- **The only candidate with a live runtime consumer.** PR #20 (commit 6357f28) already constructs a `RegulatoryDeck` with the Reg B lens in LIVE mode inside `sentinel_worker.py`'s real entrypoint, scoped to declared-proxy input screening and adverse-action reason specificity, flag-only. Every alternative (pre-foreclosure gate, loss mitigation sequencing, AVM quality control) would require building a servicing-event ingestion path or a valuation data model first, making the first regulatory lens another instance of C10.
- **It is Wm's own core use case, verbatim.** July 22: an auditor asks "can you tell me why AI made a decision to decline this application."
- **It is where the strongest federal AI-in-lending material sits.**
- **It exercises the Layer 3 edge machinery without requiring a rare conflict.** Federal ECOA and FHA against the two state statutes is a probable `floor` relationship, the most common edge type.
- **Its data already exists in the shipped cassette.** `decision.input_fields`, the recorded adverse-action reason, and the mortgage cassette's `judge()` integrity scoring all sit at this decision point.

**Standing conflict, carried from v3 and still unreconciled:** PR #20's LIVE flag-only posture is inconsistent with the Q6 lock. Whatever is currently running there is running on content that has never had a primary-text pass. This should be reconciled before more content is layered onto it, and PR #20's actual scope should be re-verified against a live clone.

### 10.2 The pilot's edge burden, revised in v4

Under 8.3, a four-lens deck requires **six** edges, not the three v3 listed.

| Pair | v3 status | Expectation |
|---|---|---|
| Reg B to HUD | listed | probably negative on this obligation |
| Reg B to Ohio | listed | expected `floor`, unverified |
| Reg B to Indiana | listed | expected `floor`, unverified |
| HUD to Ohio | **not considered** | unknown |
| HUD to Indiana | **not considered** | unknown |
| Ohio to Indiana | **not considered** | probably negative, two states do not regulate each other |

The three unconsidered pairs are the point rather than the cost. Nobody had asked whether Ohio and Indiana law interact on this obligation, and under the superseded rule nobody ever would have been asked.

### 10.3 Deliberately excluded from the pilot

All servicing, default, and disposition rules (including the Indiana settlement conference regime and the Ohio junior-lien notice statute, both researched and both slated as the natural second slice); licensing and chartering; HMDA and data reporting; NCUA institution-level rules and the preemption boundary; all Layer 1 ontology beyond what this pilot consumes.

### 10.4 Success criterion

The pilot succeeds if an auditor question of the form "why was this application declined, and does that reason satisfy your obligations" can be answered with a citation, an issuer, a provenance stamp, and an honest statement of what the system could not observe. It fails if the answer is fluent but unattributed.

---

## 11. First rule record: CFPB Reg B, specific reasons for adverse action

Drafted under 5.1 option A. Scoped to a single obligation, not to the whole of 1002.9.

**Status in v4: authoring-incomplete on two fields.** It predates 5.2, so it has no `address`, which must be retrofitted before any edge can reference it. Its `conflict_edges` field is now completable in principle, but requires six edges rather than the three contemplated below.

**`address`**: **MISSING.** Retrofit required per 5.2.

**`issuer`** (set): `{Consumer Financial Protection Bureau}`

Single-member set. **Precedent, and it generalizes:** the *enforcement* agency under 1002.9(b)(1) varies by institution type (NCUA for federal credit unions, and so on). That is not the issuer. The issuer of 12 C.F.R. Part 1002 is CFPB alone. Enforcement agency identity is record content, not lens identity. Future records must not put an enforcing agency into the `issuer` set.

**`instrument`**: Regulation B, 12 C.F.R. Part 1002. Lens: `CFPBRegBLens` (pre-existing).

**`citation`**: 12 C.F.R. 1002.9(a)(2)(i), with the specificity standard supplied by 1002.9(b)(2). Statutory basis 15 U.S.C. 1691(d)(2) and (d)(3). Official Interpretations, Supplement I to Part 1002, comments to 9(b)(2).
*Gap: paragraph lettering not checked against current eCFR text.*

**`activation_date`**:
- Current CFPB codification effective **2011-12-30** (Dodd-Frank transfer from the Federal Reserve's 12 C.F.R. Part 202).
- Underlying obligation predates it: ECOA 1976 amendments (Pub. L. 94-239), adverse action notice effective **1977-03-23**.
- *Gap, and it matters for C7:* no confirmed amendment history for 1002.9 between 2011 and August 2026. The 2023 small business lending rule amended Part 1002, but at 1002.107 and following, not 1002.9. Retroactive re-testing cannot be scoped until this is closed.

**`entity_scope`**: any "creditor" per 1002.2(l) that regularly extends credit. Reaches federal and state chartered credit unions, banks, non-bank mortgage lenders, and a CUSO that itself makes the credit decision. Does not reach a party that merely refers an applicant. 1002.9(g) allocates the notice duty when multiple creditors touch one application.
*Gap: whether a given CUSO is the creditor for a specific transaction depends on facts Sentinel may not hold.*

**`lifecycle_stage`**: `origination`.
*Ontology gap: the trigger fires at the decision point but the obligation completes after it, at notice delivery. Layer 1 needs either a post-decision notification sub-stage or an explicit convention that origination spans it. Flagged rather than invented.*

**`trigger`**: a mortgage credit application receives adverse action as defined at 1002.2(c): denial, a counteroffer the applicant does not accept, or termination or unfavorable change to an existing account. Pilot narrows to denial on a new application.

**`observable`**:
- Held: `decision.input_fields`; the recorded adverse-action reason; decision outcome; decision timestamp. The mortgage cassette's `judge()` already penalizes a denial recorded without a documented reason, so the presence half is wired.
- Not held: application-completeness timestamp, notice transmission timestamp, notice document text, presence of the ECOA statement and administering-agency name in the notice.

**`checkability`**: `hard_checkable`, in two parts.
1. Reason present on a denial. Already evaluable.
2. Reason not drawn from the disallowed set 1002.9(b)(2) names explicitly (reliance on internal standards or policies; failure to achieve a qualifying score). A list-membership test against a fixed set, which is exactly the C8 shape: it does not ask the model anything, it asks whether a recorded string matched a list.

**`opacity`**: **MARKED**, one zone.
"Specific reasons" and "principal reasons" admit more than one defensible reading that changes system behavior. The text forecloses two readings but sets no affirmative floor above them. Concrete divergence: a recorded reason of "insufficient income relative to obligations" passes a permissive reading and fails a strict reading demanding the specific model driver. Sentinel cannot resolve this (C6) and must surface it. The CFPB circulars are the natural resolver, and have no structural home (Section 9, item 2).
*Not marked:* the trigger, which is textually clear. *Deferred to the timing record:* "completed application" (1002.2(f)), which determines clock start and is genuinely contested. It changes behavior on that record, not this one.

**`conflict_edges`**: **incomplete, but no longer blocked.** v3 could not complete this field because negative edges had no defined form. 8.3 defines them. What remains is authoring work, not a design gap. Required set is the six pairs in 10.2, of which this record participates in three:
- To `HUDFairHousingLens` (24 C.F.R. Part 100): FHA prohibits discrimination but imposes no parallel adverse-action notice content requirement. Expected **negative**, with a basis stating the search scope per item 5.
- To `OhioCivilRightsLens` (R.C. Ch. 4112): expected `floor`, unverified.
- To `IndianaCivilRightsLens` (IC 22-9.5): expected `floor`, unverified.

**`provenance_stamp`**: `attested`.
Basis: authored by a model from working knowledge of Reg B's structure, not checked against eCFR text or the Official Interpretations. Per Q3 this is the ceiling a model can reach.

**Q6 status: this record cannot run LIVE in any posture, including flag-only, until a human primary-text pass upgrades it to `verified`.** Interim state is a timestamped "not running, here is what blocks it, here is the citation queue" artifact.

### 11.1 Verification queue for the human pass

1. eCFR current text of 12 C.F.R. 1002.9, including paragraph lettering.
2. Supplement I comments to 9(a) and 9(b)(2).
3. Amendment history of 1002.9 since 2011-12-30. Blocks C7.
4. Status of CFPB Circulars 2022-03 and 2023-03 as of August 2026.
5. 15 U.S.C. 1691(d) statutory text.

---

## 12. Sequence after the pilot

Proposed, not decided.

1. **Adverse action (pilot)**: origination stage, four issuers.
2. **Default and pre-foreclosure**: servicing and default stage, three issuers (CFPB Reg X, Ohio, Indiana). Where OH and IN divergence is sharpest and where `displaces` gets its first real test.
3. **NCUA institution lens and the preemption boundary**: the first real test of `displaces` between a federal prudential regulator and a state one.
4. **Ontology completion**, including the GSE, FHA, VA, and USDA overlays, correctly stamped `convention`.
5. **Remaining federal** (Reg Z, Reg C, flood, appraisal), by issuer plus instrument.

---

## 13. Precedent summary, for the author of the next record

1. A lens is scoped to an issuing authority plus a specific instrument. `issuer` is a set. Joint issuance is native, never duplicated content and never a special-case type.
2. Issuer is not enforcement agency. An agency that enforces someone else's instrument is record content, never a member of the `issuer` set.
3. **A record is scoped to one obligation, not one C.F.R. section. LOCKED.**
4. **A record's identity is an assigned address, never the content hash and never derived from citation. A separate readable label carries the name. Addresses are retired with forward pointers, never reused. LOCKED.**
5. A new authority may publish one provisional bundled lens and decompose it later via lineage, once its taxonomy is stable.
6. Anything with no issuing authority goes to Layer 1 ontology, stamped `convention`, never into a lens. Includes GSE, investor, and insurer overlays, which bind by contract, not regulation.
7. Every rule record carries address, label, issuer (set), instrument, citation, activation date, entity scope, lifecycle stage, trigger, observable, checkability, opacity, conflict edges, and provenance stamp. Every field gets a value or an explicit gap. Nothing is skipped silently.
8. Rules that cannot be checked are recorded and classified anyway, with a stated reason.
9. Opacity is marked only where the text admits multiple defensible readings that change system behavior.
10. Edges carry their own citation and their own provenance stamp, live outside both endpoints, and a coordinated multi-lens revision commits atomically or not at all.
11. **An edge pins to both endpoints' addresses and to the content hashes it was evaluated against. Any endpoint change breaks it with a typed staleness reason and blocks LIVE. LOCKED.**
12. **An edge's window is the overlap of its endpoint versions' effective windows. An author may narrow it with a citation, never widen it. A break closes a window and opens a successor edge with a new address; the old edge keeps governing the decisions it governed. LOCKED.**
13. **Any two lenses in the same deck require an edge between them, positive or negative, or the deck does not run LIVE. Negative edges are ordinary edges and go stale like any other. LOCKED.**
14. No record is stamped `verified` without a human check against primary text, and nothing runs LIVE below `verified`.
15. **Any mechanism that pre-satisfies a check for cases nobody has examined is defeating the check.** This has now eliminated three separate proposals on three different axes: wildcard edges (unexamined instruments), floating edge endpoints (unexamined versions), and author-declared completeness (unanticipated pairs). Expect it to come up again.
16. Before treating any of the above as beyond question: the boundary decision survived three independent re-derivations and the 5.1 and 8.1 decisions each survived three more, but not all were blind trials, one supporting claim from the first pass was found ungrounded on self-audit and demoted, and this document's own arbiter reached the right answer by the wrong route twice. Convergence is real evidence. It is not proof.

---

## 14. The cross-check method

Run twice now, on 5.1 and on 8.1. Documented because it is a repeatable practice and because its limits matter as much as its results.

**Procedure.** Build the strongest support case for each option in good faith, then arbitrate. Separately, send a clean-room prompt to two outside AI systems: options renamed and reordered, no recommendation, no mention that any option is already in use or already drafted against, and an explicit instruction to argue in random order. Require each arbiter to name the deciding constraint, the strongest argument against its own conclusion, any mechanism its winner needs, and any fourth option.

**Results so far.** Both runs produced 3-of-3 convergence on the answer. Both runs also produced a fourth option from the outside arbiters that this document's author had not generated. In both runs this document's arbiter reached the correct verdict through a weaker elimination order than the outside arbiters used.

**Known limits, stated plainly.**
- Convergence on 5.1 was weakened by the fact that the constraint both outside arbiters found decisive ("exactly one value per field") was this framework's own prior design choice, not a property of regulation.
- Facts that lean cannot be withheld without introducing worse bias. Both prompts included counter-pulling facts deliberately.
- **A withheld gap (item 6) was found by neither outside arbiter across two independent passes.** Three arbiters agreeing does not mean three arbiters looked everywhere.

---

## 15. Provenance of this document

C4 applied to this document itself.

**Reasoning** (Sections 4, 5, 8, 9, 10, 11): authored across four sessions, cross-checked by four independent outside opinions across three structural forks, and self-audited once for single-sided reasoning. The self-audit found and corrected one ungrounded claim; it did not overturn the conclusion.

**Regulatory findings** underlying the pilot's issuer set, citations, and Section 11's content: all stamped `attested` at best. Section 11 in particular was authored from a model's working knowledge of Reg B, not from the regulation's text. Its citation lettering, activation dates, and the exact wording of 1002.9(b)(2)'s disallowed reasons are all unverified. Prior state-level findings came from secondary material (Justia, FindLaw, law firm commentary, a county law library guide). Open gaps: status of CFPB Circulars 2022-03 and 2023-03 as of August 2026 unconfirmed; absence of an Ohio or Indiana AI-specific lending statute unobserved rather than affirmatively confirmed; whether either state has a community-reinvestment equivalent reaching credit unions not established.

**Code and repository claims** (PR numbers, commit hashes, what is wired where): drawn from prior session records, not a live clone. PR #20's actual scope should be re-verified, with added urgency given the Section 10.1 note about its LIVE posture conflicting with Q6.

**One claim in this version rests on general pattern rather than a confirmed instance:** that agencies renumber paragraphs during amendments, which is part of why a citation-derived address was rejected in 5.2. No historical renumbering inside 12 C.F.R. Part 1002 has been verified. If that verification matters, it belongs on the 11.1 queue.

**Locked versus recommended:** Sections 3 (except Q7 and Q9), 4.5, 4.6, 4.7, 5.1, 5.2, 8.1, 8.2, 8.3, 10, and precedent items 1 through 15 are LOCKED. Section 9's seven items are open and must not be treated as settled.

---

*End v4.*
