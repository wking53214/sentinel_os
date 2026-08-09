# Mortgage Regulatory Lens Framework
## Concept Design and Precedent Record — v2

**Version:** v2, supersedes v1 (2026-08-07)
**Date:** 2026-08-08
**Status:** Architecture LOCKED where marked. Zero rule content authored. Zero code written.
**Scope:** Establishes the authoring framework for regulatory cassette lens content, using mortgage as the first vertical. Every subsequent regulatory lens is expected to inherit this framework, which is the reason for the level of detail below.
**Baseline:** sentinel_os origin/main at 44d26df (per the Aug 7 roadmap walkthrough). Nothing in this document has been verified against a live clone in the sessions that produced it.
**Continuity note:** this document is written to be the sole artifact a new chat session needs to pick this work up without re-deriving anything below. If you are that session: read this whole file before authoring any rule record.

---

## 0. Changelog from v1

v1 recommended a single structural answer (one lens per issuing authority) and left five open questions. Since then:

- All five of v1's open questions (Q1-Q5) were answered by Wm. See Section 3.
- v1's core structural recommendation was **revised**, not confirmed. The lens boundary is issuer **plus instrument**, not issuer alone. This surfaced because the naming convention question (v1's Q4) exposed a contradiction: the codebase's existing `CFPBRegBLens` is already instrument-scoped, and v1's stated Decision 1 was not. See Section 4.
- That revision was independently cross-checked twice more, once by a second AI system reasoning from a neutral framing of the same fork, once by a third opinion that additionally produced a concrete mitigation menu. All three conclusions converged. See Section 4.4.
- A new open question (Q6) was raised and is **not yet answered**. See Section 8.
- Several items raised during the revision process were never individually confirmed by Wm before the conversation moved on. They are recommendations, not locks, and are flagged as such throughout. See Section 8.
- The pilot (adverse action, four issuers) is now **explicitly confirmed** by Wm. In v1 it was only proposed.
- One correction to v1's own pilot framing: the state issuers for the adverse-action pilot are the **Ohio Civil Rights Commission** and **Indiana Civil Rights Commission**, not the state DFIs. v1 did not specify this precisely enough.

Everything not called out above (Sections 1, 2, 5, 6, 7, 9-13 structure) is materially unchanged from v1 and is restated here for completeness, since this document needs to stand alone.

---

## 1. The question this document answers

Wm's stated goal: a production-ready, not MVP, Sentinel OS for mortgages, prepared for any auditor's question no matter how vague or opaque.

That goal produced a concrete structural question:

> Should mortgage regulation be encoded as one cassette covering all of mortgage lending, several cassettes split by issuing authority, or something finer still?

And two follow-ons, now both resolved:

> Given the answer, what is the smallest vertical slice that can be built first without setting bad precedent? **(Answered: adverse action, four issuers. Section 5.)**
> How do future lens authors extend this without re-deriving it from scratch? **(This document.)**

---

## 2. Constraints inherited from sentinel_os

Unchanged from v1. Restated for completeness, since a future reader needs to know which constraints are load-bearing and where they came from.

**C1. The adoption model is agency-scoped.** Wm's stated model (July 22): an auditor from an agency walks into a regulated company and plugs in that agency's cassette; each agency publishes one official cassette all its auditors use.

**C2. Cassettes are content-hash bound.** A change to a cassette's content changes its hash and forces a version bump. Binding enforcement is live (closed July 22).

**C3. Cross-lens conflict resolution already exists.** `resolve_tier_conflict` is wired into `RegulatoryDeck.judge()`/`explain()` (commit 2ab81e3). A deck holds multiple lenses and detects disagreement between them.

**C4. The Provenance Rule.** Locked, Wm's own final wording: *"Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."*

**C5. Never a fabricated, fake, or guessed value.** `estimated` is permitted as a stamp so long as it is labeled and never interchangeable with `verified` or `attested`.

**C6. Ambiguity is surfaced, never resolved by the system.** Wm: *"I never want Sentinel to make a decision with ambiguity."* Regulation cassettes hold regs plus explicitly marked opaque zones.

**C7. Interpretation versions bind to a regulation's activation date.** Retroactive re-testing is mandatory when a new reg has an activation date.

**C8. The COBOL principle.** Wm's 15-year framing: a list-membership test does not get easier to defeat as models improve, because it never asks the model anything. It asks whether a past fact was on a list.

**C9. The observer posture.** Sentinel is a judge/witness, not an actor. Core use case (July 22): an auditor asks *"can you tell me why AI made a decision to decline this application."*

**C10. The recurring defect shape.** Real, fully-tested code that no entrypoint ever constructs. Any new build must have a runtime consumer or it becomes another instance.

---

## 3. Wm's locked answers to v1's open questions

**Q1 — Does the Provenance Rule gain a fourth stamp?** **LOCKED: yes.** `convention` added alongside verified/attested/estimated, for Layer 1 ontology — material that is true and universal but has no issuing authority. This changes the wording of a rule Wm locked personally. A guard on this stamp is recommended but not yet confirmed; see Section 8, item 3.

**Q2 — Does a lens ever refuse to load without its edges?** **LOCKED: option C.** A lens declares which other issuers it expects edges to, and the deck refuses to run LIVE if a declared edge is missing. The fail-closed choice. A refinement to prevent this from becoming self-defeating (bootstrapping trap, negative edge records, bidirectional declaration, raise-not-degrade on a missing edge) was proposed but never individually confirmed; see Section 8, item 1.

**Q3 — Who authors, and what is the review gate?** **LOCKED: proposal accepted.** A model may author rule records at `attested` and `estimated` stamps only. `verified` requires a human check against primary agency or legislative text. Mirrors the interpretation framework's legal-approval gate.

**Q4 — Lens naming and versioning convention.** **RESOLVED, by the boundary decision itself.** See Section 4. Pattern: `<Issuer><Instrument>Lens`, e.g. `CFPBRegBLens` (pre-existing), `OhioCivilRightsLens`, `IndianaFairHousingLens`.

**Q5 — Is a coarser fallback acceptable if per-issuer authoring proves too slow?** **Answered, read as: proceed, do not block on perfecting granularity.** Wm's response: resolving issuer granularity "perfectly" is an oxymoron, since nothing can be perfect.

---

## 4. The lens boundary — LOCKED, with full decision history

This is the single most consequential structural decision in this document, and Wm asked explicitly for the reasoning behind it to be documented to the minutia, since every future lens inherits it. The full arc is recorded here rather than only the conclusion, because the arc is itself part of the precedent: this decision was independently re-derived three times by three different reasoning processes before being locked, and that track record is part of why it can be trusted going forward.

### 4.1 v1's original position, and the contradiction that reopened it

v1 recommended **one lens per issuing authority**, full stop. The reasoning was sound as far as it went: it matched Wm's stated adoption model (C1), it kept hash-binding blast radius scoped to what actually changed (C2), and it gave `resolve_tier_conflict` (C3) real work to do.

The contradiction surfaced during the Q4 naming discussion. The existing, already-shipped `CFPBRegBLens` is not scoped to the issuing authority. It is scoped to the issuing authority **plus one specific regulation**, Reg B. Under a strict reading of v1's Decision 1, the CFPB should have exactly one lens covering Reg B, Reg X, Reg Z, Reg C, and Reg F all together. That is not what exists, and building toward it would have meant renaming or reworking a live artifact to fit a document that had never actually been checked against it.

### 4.2 The fork, stated cleanly

**Option A — one lens per issuer.** A regulator with six regulations gets one lens containing all six.

**Option B — one lens per issuer plus instrument.** A regulator with six regulations gets six lenses.

### 4.3 Why B, first pass

- **Blast radius.** Under A, an amendment to any one of the CFPB's six regulations rehashes the entire CFPB lens, voiding the binding for every lender whose use case never touched the amended regulation. This is the identical defect that killed v1's single-mortgage-lens option, recurring one level down. A false tamper signal under a hash-binding trust model is worse than no signal, because it teaches the audience to ignore version bumps.
- **Activation dates.** C7 binds interpretation versions to a regulation's activation date. A lens holding six regulations has six activation dates and no single honest answer to "when did this lens's content take effect."
- **It matches the code.** `CFPBRegBLens` already exists. B costs zero renaming. A would require either renaming a live artifact or accepting a permanent mismatch between the framework document and reality.
- **It degrades gracefully.** An issuer with exactly one instrument produces an identical result under A or B. A new, small authority (see the FRED case below) costs nothing extra under B.

The honest cost of B, acknowledged at the time: more artifacts to author and load, and a genuine open question about how a rule jointly issued by several authorities at once should be represented, since B's "one issuer per lens" framing as originally stated didn't obviously accommodate that case.

### 4.4 Three independent cross-checks, same conclusion

**Cross-check 1 — the FRED exercise.** Reasoning from the perspective of a hypothetical new regulatory agency twenty years out, in an industry that does not yet exist, surfaced two things v1's mortgage-only framing could not: first, that a young, fast-amending authority hits blast-radius damage in months rather than the decade a mature agency like the CFPB would take, which is a stronger argument for B than the original one; second, that the interagency AVM rule (co-issued by NCUA, OCC, the Fed, FDIC, CFPB, and FHFA) is a real, present-day example of the joint-issuance case B's original framing hadn't resolved. This produced the requirement that **`issuer` be a set, not a scalar**, so a single lens can natively hold multiple issuers rather than being duplicated once per issuer or forced into a special-case type.

This cross-check was later self-audited and found to be a generative exercise, not a verification one: it was run single-sided (B was never argued against by an independent process), and one of its supporting claims (that false tamper signals erode trust "within a year") was found on re-examination to be plausible narrative with no grounding, and was demoted. The issuer-as-set finding survived the audit, because it traced to a real, checkable fact (the AVM rule's six co-issuers) rather than to narrative plausibility.

**Cross-check 2 — an independent AI system, given a neutral framing of the same fork.** Converged on B. Contributed two points not previously surfaced: first, that edges between instrument-level lenses carry precise semantic meaning ("Instrument X overrides Instrument Y"), while edges between authority-level lenses are ambiguous ("something in A relates to something in B"), which is a direct strengthening of the C3/Q2 edge machinery; second, a formal mechanism for the "new authority doesn't know its taxonomy yet" problem, that a provisional single lens should remain historically addressable and later decompose into multiple instrument-level lenses via a structural migration with its own seals, rather than an ordinary edit. This is now the locked **lineage/decomposition mechanism**, Section 4.6.

This cross-check is genuine independent corroboration but not a blind trial: the prompt that produced it described both sides of the fork and named the joint-issuance case as decisive before the second system reasoned about it. It could have argued for A regardless and did not, which is real signal, but the framing was not neutral in the strongest sense.

**Cross-check 3 — a third opinion, given the same joint-issuance test case.** Converged on B a third time, independently. Contributed a concrete mitigation menu for B's known cost (edge-declaration burden scaling with instrument count rather than authority count): a rejected option (wildcard/group edges) and an adopted one (deck-level atomic multi-lens updates). See Section 4.7.

### 4.5 Decision, final

**LOCKED: lens identity = issuing authority plus instrument. `issuer` is a set on the lens record, not a scalar.**

A lens may have one or many issuers. An authority may issue one or many instruments. Joint issuance is represented natively, one lens with multiple issuer tags, never as duplicated content across several lenses and never as a special-case lens type.

### 4.6 The lineage/decomposition mechanism — LOCKED

A new authority may publish one provisional, bundled lens before its instrument taxonomy is stable. When the taxonomy stabilizes, the bundle is formally decomposed into separate instrument-scoped lenses through a structural migration, not an ordinary content edit. The original provisional lens remains historically addressable so that a decision governed under it stays queryable; the new lenses receive their own independent seals.

This preserves the real insight underneath Option A (a young authority should not be forced into premature structure) without adopting Option A's blast-radius defect once the taxonomy is known.

### 4.7 The edge-burden mitigation menu — LOCKED (2 of 3 adopted)

The known cost of B is that edge-declaration burden under Q2/option C scales with instrument count, which is larger than authority count. Three candidate mitigations were evaluated:

**REJECTED — wildcard or group edges** (a lens declares a single edge to `Authority:Regulator_Name:*` instead of one edge per instrument). Rejected because it directly defeats Q2's fail-closed guarantee: a wildcard auto-satisfies the edge-declaration check for every instrument that authority ever publishes, including ones that do not exist yet, meaning the specific evaluation Q2 exists to force never actually happens for anything the wildcard covers. This reduces cost by weakening the protection itself, which is not a real mitigation.

**ADOPTED — the sandbox/lineage mechanism**, Section 4.6. Solves "the authority's own taxonomy is not yet stable."

**ADOPTED — deck-level atomic multi-lens updates.** Solves a distinct problem lineage does not cover: an authority with a *stable* taxonomy deliberately revising several of its own instruments together, as one coherent policy act. The deck compiler validates the full multi-lens cluster in memory before committing, treating it as a single atomic change, so a coordinated revision does not have to pass through an inconsistent intermediate state where some of the cluster's new versions exist and others do not. This has real precedent already built into the repo: the interpretation framework's anchoring design (Aug 6) commits to the same discipline one layer up, fail-closed on the anchor write, an event does not report success unless it fully completes.

---

## 5. The rule record schema

Unchanged from v1. Research output is authored **as** these records, not as prose, so that turning research into lens content is a transcription with a diff, not an unaudited interpretation step.

- **`issuer`** — a SET of issuing authorities (revised from v1's scalar framing per Section 4.5).
- **`instrument`** — the specific regulation, statute, or rule this record belongs to. New field, made necessary by Section 4's resolution; v1 did not have this because v1 had not yet separated issuer from instrument.
- **`citation`** — exact section. Never "state law" or "federal law" generically.
- **`activation_date`** — required for C7. Where a rule has been amended, both the current activation date and enough history to answer a question about a decision made under a prior version.
- **`entity_scope`** — credit union, CUSO, state charter, federal charter, servicer, and whether the rule reaches the institution or only a subsidiary.
- **`lifecycle_stage`** — origination, servicing, default, disposition. References Layer 1 ontology.
- **`trigger`** — the event that makes the rule apply. A rule with no expressible trigger is a strong signal it belongs in the not-checkable class.
- **`observable`** — the specific data Sentinel must already hold to evaluate the rule. Should name actual fields where they exist (e.g. `decision.input_fields`, `resolved_value.resolution_type`), not describe data abstractly.
- **`checkability`** — `hard_checkable`, `attestable_only`, or `outside_observation`. See Section 6.
- **`opacity`** — whether the rule's text admits more than one defensible reading that would change system behavior. See Section 7.
- **`conflict_edges`** — references to Layer 3 edge records, Section 8 below (unchanged, `displaces`/`floor`/`conflicts`).
- **`provenance_stamp`** — `verified`, `attested`, `estimated`, or `convention` (Layer 1 ontology only, per Q1).

A field considered and dropped in v1, still dropped: `severity`/`risk_weight`. Assigning severity to a regulation is compliance adjudication, which Sentinel does not do (per the July 23 escalate/review_priority precedent).

---

## 6. Checkability classes

Unchanged from v1.

**`hard_checkable`** — evaluable from data Sentinel already holds at decision time.
**`attestable_only`** — the obligation is real, but Sentinel can only record the lender's assertion of compliance, stamped with its own provenance.
**`outside_observation`** — the rule is real and cited, but nothing in Sentinel's data model touches it.

All three are retained, none discarded. Silence is indistinguishable from ignorance. A rule stamped `outside_observation` with a stated reason is a defensible answer to an auditor; an absent rule is not, and the absence cannot be told apart from an oversight.

---

## 7. Opacity marking

Unchanged from v1. Marked only where the regulation's own text admits more than one defensible reading that would produce different system behavior, never merely because a rule is complex. Over-marking converts the interpretation framework's legal-review gate from a governance asset into a standing tax the legal team will eventually stop paying.

---

## 8. Open items — NOT locked, flagged for Wm's decision

These were raised during the revision process and never individually confirmed. They should not be treated as settled, and a future session should not assume they are.

**Item 1 — Q2's bootstrapping trap.** As stated, option C (a lens declares expected edges and the deck refuses LIVE if one is missing) has a self-defeating property: the first lens ever built has no peers to declare edges to and could never run, and every new lens added would retroactively break every deck already running against an older lens that didn't anticipate it. Proposed fix, **not yet confirmed**: the declaration requirement is satisfied by an edge record, including a *negative* one ("evaluated, no relationship found, here is the basis"), not by the other lens existing yet. Two riders proposed alongside it: declaration should be checked bidirectionally, since a one-sided declaration means only one author actually considered the pair; and a missing edge must raise a typed error rather than silently degrade to an empty edge set, the same failure mode already caught once in the F4 BISG geocoder guard fix.

**Item 2 — Q6, new question.** Does LIVE mode require `verified` content, or is `attested` sufficient as long as the stamp is disclosed? Under Q3's lock, the pilot will ship entirely `attested` at best, since no primary-text human pass has occurred. Recommendation offered but not confirmed: `attested` is sufficient for LIVE while the posture is flag-only (an honest, disclosed-provenance flag is a legitimate artifact), but `verified` should be required before any lens moves to an enforcing posture.

**Item 3 — Q1's guard.** `convention` should not function as a confidence level sitting below `estimated`. It is orthogonal: it means "no issuing authority exists for this," not "we are less sure." Proposed guard, not yet confirmed: `convention` is valid only in Layer 1 ontology, and still requires a stated basis even without a citable issuer.

**Item 4 — pilot issuer correction.** Carried forward from the pilot correction below; flagged here because it was a self-correction mid-session and Wm has not explicitly acknowledged it.

None of these block starting the first rule record. All of them should be resolved before the pilot's content is treated as final.

---

## 9. Pilot — CONFIRMED

**CONFIRMED by Wm: adverse action on a declined mortgage application, across four issuers.**

- **CFPB** — Regulation B (adverse action notice content and timing, 12 C.F.R. 1002.9), and the CFPB's circulars on adverse action where a credit decision relies on a complex or opaque model.
- **HUD** — Fair Housing Act, 24 C.F.R. Part 100.
- **Ohio** — R.C. Ch. 4112, administered by the **Ohio Civil Rights Commission**. Correction from v1: this is not the Ohio Division of Financial Institutions. OCRC and the Ohio DFI are separately examining authorities and would be separate lenses under Section 4's framework regardless.
- **Indiana** — IC 22-9.5, administered by the **Indiana Civil Rights Commission**. Same correction: not the Indiana DFI.

### 9.1 Why this pilot

- **The only candidate with a live runtime consumer.** PR #20 (commit 6357f28) already constructs a `RegulatoryDeck` with the Reg B lens in LIVE mode inside `sentinel_worker.py`'s real entrypoint, scoped to declared-proxy input screening and adverse-action reason specificity, flag-only. Every alternative candidate evaluated (the pre-foreclosure gate, loss mitigation sequencing, AVM quality control) would require building a servicing-event ingestion path or a valuation data model first, which would make the first regulatory lens another instance of C10's recurring defect shape.
- **It is Wm's own stated core use case, verbatim.** July 22: an auditor asks "can you tell me why AI made a decision to decline this application," and Sentinel is the system that answers that.
- **It is where the strongest federal AI-in-lending material sits.** The CFPB's circulars on adverse action for complex models attach directly to this decision point, and the state-level research conducted so far found no Ohio- or Indiana-specific AI lending statute, so this pilot is likely to remain the richest AI-relevant material in the corpus for some time.
- **It exercises the Layer 3 edge machinery without requiring a rare conflict.** Federal ECOA/FHA against Ohio R.C. Ch. 4112 and Indiana IC 22-9.5 is a probable `floor` relationship (state law providing a floor at or above the federal one), the most common edge type, and the right one to prove the mechanism against first.
- **Its data already exists in the shipped cassette.** `decision.input_fields`, the recorded adverse-action reason, and the mortgage cassette's own `judge()` integrity scoring (which already penalizes a denial recorded without a documented reason) all sit at exactly this decision point.

### 9.2 Deliberately excluded from the pilot

- All servicing, default, and disposition rules — out of lifecycle scope, and includes the Indiana settlement conference regime and the Ohio junior-lien notice statute, both researched and both slated as the natural second slice.
- Licensing and chartering — governs whether the institution may operate, not whether a given decision was sound; does not fit the trigger/observable model.
- HMDA and data reporting — institution-level periodic obligation, not per-decision.
- NCUA institution-level rules and the preemption boundary — deferred; state civil rights statutes are a weaker preemption case than state lending statutes, so this pilot only lightly touches preemption.
- All Layer 1 ontology beyond what this pilot consumes.

### 9.3 Success criterion

The pilot succeeds if an auditor question of the form "why was this application declined, and does that reason satisfy your obligations" can be answered with a citation, an issuer, a provenance stamp, and an honest statement of what the system could not observe. It fails if the answer is fluent but unattributed, which is the exact failure mode this framework exists to prevent.

---

## 10. Sequence after the pilot

Proposed, not decided.

1. **Adverse action (pilot)** — origination stage, four issuers.
2. **Default and pre-foreclosure** — servicing/default stage, three issuers (CFPB Reg X, Ohio, Indiana). This is where OH/IN divergence is sharpest (Indiana's settlement conference against Ohio's discretionary mediation) and where the `displaces` edge type gets its first real test.
3. **NCUA institution lens and the preemption boundary** — the first real test of the `displaces` mechanism between a federal prudential regulator and a state one.
4. **Ontology completion**, including the GSE/FHA/VA/USDA overlays, correctly stamped `convention`.
5. **Remaining federal** (Reg Z, Reg C, flood, appraisal), by issuer plus instrument.

---

## 11. Precedent summary, for the author of the second lens

1. A lens is scoped to an issuing authority plus a specific instrument. `issuer` is a set. Joint issuance is native, never duplicated content and never a special-case type.
2. A new authority may publish one provisional bundled lens and decompose it later via lineage, once its taxonomy is stable.
3. Anything with no issuing authority goes to Layer 1 ontology, stamped `convention`, never into a lens. This includes GSE, investor, and insurer overlays, which bind by contract, not regulation.
4. Every rule record carries issuer (set), instrument, citation, activation date, entity scope, lifecycle stage, trigger, observable, checkability, opacity, conflict edges, and provenance stamp.
5. Rules that cannot be checked are recorded and classified anyway, with a stated reason. Coverage gaps are documented, never silently omitted.
6. Opacity is marked only where the text admits multiple defensible readings that change system behavior.
7. Edges carry their own citation, live outside both endpoints they connect, and a coordinated multi-lens revision commits atomically or not at all.
8. No rule record is stamped `verified` without a human check against primary text; a model may author `attested` or `estimated`.
9. Before treating any of the above as beyond question: it survived three independent re-derivations, but two of those were not blind trials, and one supporting claim from the first pass was found ungrounded on self-audit and demoted. Convergence is real evidence. It is not proof.

---

## 12. Provenance of this document

Per C4, applied to this document itself.

**Reasoning** (Sections 4, 8, 9): authored across two sessions, cross-checked by two independent outside opinions on the central structural fork (Section 4.4), and self-audited once for single-sided reasoning (the FRED exercise). The self-audit found and corrected one ungrounded claim; it did not overturn the conclusion.

**Regulatory findings** underlying the pilot's issuer set and citations: all stamped `attested` at best. Sourced from secondary material (Justia, FindLaw, law firm commentary, a county law library guide). None verified against primary agency or legislative text. Known gaps, carried forward from v1 and still open: current status of CFPB Circulars 2022-03 and 2023-03 as of August 2026 unconfirmed; absence of an Ohio or Indiana AI-specific lending statute unobserved rather than affirmatively confirmed; whether either state has a community-reinvestment equivalent reaching credit unions not established.

**Code and repository claims** (PR numbers, commit hashes, what is wired where): drawn from prior session records, not a live clone checked in the sessions that produced this document. PR #20's actual scope should be re-verified against the repo before the pilot's content is built on top of it.

**What is locked versus what is recommended:** Sections 3, 4.5, 4.6, 4.7, and 9 are LOCKED, confirmed explicitly by Wm. Section 8's four items are recommendations awaiting his decision and must not be treated as settled by a future session.

---

*End v2.*
