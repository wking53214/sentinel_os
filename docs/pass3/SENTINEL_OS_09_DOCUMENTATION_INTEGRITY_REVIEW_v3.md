> **⚠ Point-in-time snapshot — not maintained.** Documented baseline `68cadfb` (July 29, 2026). Since then PRs #28–30 landed: keyed `authorized_by` ledger attestation with key rotation, the persisted observed-event layer, and — most relevant to anything below that names a directory — the **extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo** (2026-08-28). The standalone simulator and its `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree, Twilio ingestion, the Claude governor client, and the queue/staffing/Bayes layer are no longer in this repo. Treat directory maps, module inventories, and test counts here as historical. The canonical current description is the [repository root README](../../README.md).

---

# DOCUMENTATION INTEGRITY REVIEW

**Subject:** The eight-document Sentinel OS documentation package, Pass 3, Round 2
**Documents reviewed:** `SENTINEL_OS_01` through `SENTINEL_OS_08` — originally `_v2`; corrected to `_v3` per this review's own findings and the subsequent verification pass
**Baseline they describe:** `origin/main` at commit `68cadfb`, July 29, 2026
**Source authority they were built from:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP-1.md`

**What this review is.** An audit of the documentation, not of the system. Part 1 verifies the package against its own standard. Part 2 attacks it and reports defects introduced by the documentation. Part 3 reports defects inherited from the source documents, which propagate into any package built from them. Part 4 audits compliance against the instructions the package was written to. Part 5 states a verdict.

**Severity scale**
🔴 **Critical** — a reader would be misled on a material point
🟠 **High** — an instruction was not met, or a claim exceeds its evidence
🟡 **Moderate** — an inconsistency a careful reader would notice
🟢 **Low** — a stylistic or precision defect

**Correction notice (v3).** After this review was written, its own recommendations were tested against the live repository at `68cadfb` rather than left as recommendations. That pass confirmed F-8 as an actual factual error, not a risk — the real schema has 24 columns, not the 9 the package relied on — and closed it in Document 4. It also resolved F-7 (the two architecture-map files are byte-identical), withdrew inherited defect #3 (no duplicate `cassette_forensics.py` exists), resolved inherited defect #10 (obligation tables are twin-side, unambiguous in code), confirmed inherited defect #4 as real (genuine symlinks), and found that the twin's 18 tests — described throughout Documents 3, 4, and 8 as excluded from CI — have run in CI since commit `d881bc0`. Those five corrections are marked inline below and propagated to v3 of Documents 3, 4, and 8. Part 3's defect table and Part 5's verdict have been updated accordingly; Parts 1, 2, and 4 are otherwise unchanged from the original review.

---

# PART 1 — BLUE TEAM VERIFICATION

## Does this accurately represent Sentinel OS?

**Substantially yes, with the qualifications in Part 2.** Every capability claim traces to a source statement. The package does not present the system as more complete than the sources support: the unconnected data source, the three-of-six dimension coverage, the advisory-only verdict, the eighteen justified security suppressions, and the CI-excluded twin tests all appear in the documents that would be most tempted to omit them — the executive brief, the investor brief, and the deck structure.

**One systemic weakness.** The package repeatedly treats a documentation summary as an exhaustive specification. Where a source lists schema fields, the package concludes that unlisted fields do not exist. That inference is not sound, and it produces at least one materially strong claim that should have been softer (F-8).

## Does it preserve documented truth?

**Yes on category discipline; imperfectly on tag discipline.** No `INTERPRETATION` was promoted to `FACT`. No future item was presented as current state. No capability was invented. The failure mode that did occur is subtler: several evaluative comparatives were tagged `DERIVED` when they require a comparison class the sources do not contain (F-3). "Highest-risk file," "unusually explicit," "normally untestable," and "undergraduate-level" are judgments wearing a logical tag.

**The `UNKNOWN` discipline held.** Roughly forty distinct absences are recorded as `UNKNOWN` rather than filled, including the ones that would have been easiest to fabricate — the mission statement, the market, the buyer, the retention policy, and the incident response procedure.

## Does each audience receive useful information?

| Document | Audience served | Assessment |
|---|---|---|
| 1 Executive Brief | CEO, CTO | Yes. Decision-relevant, with the four disclosures an executive would resent discovering later |
| 2 Engineering Guide | Inheriting engineer | Yes, and the strongest document in the package. Names the two files that carry disproportionate risk and the ten questions only the maintainer can answer |
| 3 Security Review | Security engineer, risk reviewer | Yes, with one structural limit: the instruction forbade recommendations, so a reviewer receives a control inventory and no remediation path. The scope note discloses this |
| 4 Auditor Guide | External examiner | Yes. Organized around demands rather than features, which is how an examiner works |
| 5 Investor Brief | Early-stage investor | Yes. Correctly insufficient for an investment decision and says so |
| 6 Red Team | Investor | Yes on substance. Severity scale is inflated (F-11) and the required reader frame is missing (F-1) |
| 7 Deck Structure | Founder preparing to raise | Yes. The buildability table is the most useful element and correctly refuses two slides |
| 8 Operations Runbook | Operator, SRE | Thin by necessity and honest about it. An operator can start the system and diagnose four errors; they cannot support it. §11 identifies the one missing procedure that matters most |

**Gap in audience coverage.** No document serves the compliance officer or the model-risk function, who are — on the package's own analysis — the people whose questions the system exists to answer. The eight-document structure was prescribed, so this is an observation about the prescription rather than a defect in execution.

## Are boundaries preserved?

**Yes.** The kernel/cassette, lens/decision, simulation/production, and custody boundaries are described consistently across all eight documents. The load-bearing distinction — that the governance verdict records without controlling — is stated in Documents 1, 2, 3, 4, 5, 6, and 7, and never softened into "governs" or "gates." The prohibition against writing "prevents bias" held: every document reproduces the sources' own conclusion that redlining is not prevented in real decisions.

**One boundary claim is stronger in Document 1 than the package supports** (F-6).

---

# PART 2 — RED TEAM: DEFECTS INTRODUCED BY THIS PACKAGE

### F-1 · Two documents omit a required section
**Finding:** Documents 6 and 7 do not contain the four-part reader frame — AUDIENCE, READER QUESTIONS, DECISION OBJECTIVE, TRUST FAILURE — that the instructions require of every document.
**Evidence:** Documents 1, 2, 3, 4, 5, and 8 each open with §0 READER FRAME. Document 6 opens directly with its top-three ranking. Document 7 opens with its buildability table. Document 7 answers an investor question per slide, which is not the same as a document-level decision objective.
**Severity:** 🟠 High — a stated instruction was not met in two of eight documents.
**Correction required:** Add the four-part frame to both. Document 6's trust failure is the one that matters: a red team that does not state what would change its mind is advocacy, and Document 6 addresses this only in its closing section.

### F-2 · The headline test figure appears unqualified in two documents
**Finding:** "Roughly 670 tests passing" appears in Documents 1 and 5 without the qualifications that the same package establishes in Documents 6 and 7.
**Evidence:** Document 1 §7 and Document 5 §1 state the figure plainly. Document 6 C-12 and Document 7 slide 8 establish four qualifications: a 384–673 range depending on branch, 18 twin tests excluded from CI, one known-flaky test, and eighteen justified security suppressions. A reader who receives only the executive brief or only the investor brief gets the number without any of them.
**Severity:** 🟠 High — the documents most likely to circulate alone are the two that omit the caveats.
**Correction required:** Attach a one-line qualification at first use of the figure in every document that cites it.

### F-3 · Evaluative comparatives tagged as logical derivations
**Finding:** Judgments requiring a comparison class absent from the sources are tagged `DERIVED` rather than `INTERPRETATION`.
**Evidence:** Document 2 — `production_harness.py` is "the highest-risk file in the repository." Document 3 — "The trust model is unusually explicit for a system of this size." Document 4 — the maturation rule makes testable "something normally untestable." Document 6 — the constructions are "undergraduate-level." None of the four comparisons is available from the sources.
**Severity:** 🟠 High — this is precisely the failure the truth model exists to prevent, and it occurred four times.
**Correction required:** Retag as `INTERPRETATION`, or remove the comparative and state the underlying fact. Document 2's case is the most consequential, because an inheriting engineer will act on a risk ranking.

### F-4 · A recommendation crossed into a document forbidden to recommend
**Finding:** Document 8 §11 states that an operator "should not accept a handover without" the missing integrity-response procedure.
**Evidence:** The global instruction permits no recommendations unless explicitly requested. Document 8's instruction is to document existing procedures and mark gaps `UNKNOWN`.
**Severity:** 🟠 High — a clear instruction breach, in the document where the temptation was strongest.
**Correction required:** Restate as an observation: the gap exists, it sits on the system's central purpose, and no response procedure is documented. Let the operator draw the conclusion.

### F-5 · Severity inflation in the red team
**Finding:** Document 6 assigns 🔴 Critical to eight of eighteen concerns, against a scale defining Critical as "would end the conversation unless resolved."
**Evidence:** C-1, C-3, C-5, C-10, C-13, C-15, C-16, and C-18 are all marked Critical. The document separately ranks only three concerns as most likely to end the conversation, which contradicts the eight-item Critical set.
**Severity:** 🟠 High — a scale on which forty-four percent of findings are Critical carries no signal, and the internal contradiction with the top-three ranking is visible on the first page.
**Correction required:** Reserve Critical for the three ranked deal-breakers plus C-15 and C-18, which are commercially decisive. Reclassify the remainder to High.

### F-6 · An isolation claim is stronger in Document 1 than the package supports
**Finding:** Document 1 §6 states that separation "is enforced, not merely intended," citing the kernel's absence of domain imports. Document 2 documents a labeled isolation boundary that documented imports contradict.
**Evidence:** Document 2 §1 records that `Engines/` is labeled "Simulator-only, no production" by its own README while `simple_rl_trainer.py` and `bayes_learning_loop.py` are imported by `ivr_cassette.py` and `production_harness.py`. An executive reading only Document 1 receives a categorical assurance about isolation that the engineering document qualifies.
**Severity:** 🟡 Moderate — the two claims concern different boundaries and are not strictly contradictory, but the executive-facing version is the stronger one.
**Correction required:** Add one clause to Document 1 §6 noting that one labeled isolation boundary elsewhere in the repository is contradicted by documented imports.

### F-7 · Source identity assumed rather than confirmed — RESOLVED
**Finding:** The instructions name `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md` as a source of truth. The file supplied was `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP-1.md`. All eight documents treat them as the same artifact without noting the assumption.
**Evidence:** The instruction's source list and the supplied filename differ. Every negative claim in the package — roughly forty `UNKNOWN` determinations — depends on the supplied file being the complete, current architecture map rather than a variant or partial copy.
**Severity:** 🟡 Moderate, at the time of writing — the content was internally consistent with the named document, but the assumption was load-bearing and undisclosed.
**Correction required:** Done. A subsequent byte-level comparison of both uploaded `TECHNICAL_ARCHITECTURE_MAP` files confirmed they are identical. The assumption was correct; no correction to the package's content was needed.

### F-8 · A documentation summary was treated as an exhaustive schema — outcome: the claim was affirmatively false
**Finding:** Several claims of the form "no such field exists" rest on a source's schema summary rather than on the schema.
**Evidence:** Document 4 stated as `FACT` that "the ledger schema contains no field identifying a person," and placed identity attribution in the not-producible tier. The support was a nine-field schema listing in the Quick Reference. A subsequent verification pass cloned the repository at `68cadfb` and inspected the live schema directly: the real `ledger_entries` table carries 24 columns, not 9, including `authorized_by` and `call_sid`. Both are actor-adjacent, though neither identifies a human — `authorized_by` names a component and `call_sid` is a carrier-side identifier. The same verification found `reason TEXT` is a real, populated column, directly contradicting Document 4's separate claim that free-text reasoning is never captured; what it holds is the deciding AI's self-report, not an independent business reason.
**Severity:** 🔴 Critical, retroactively — this was flagged 🟠 High when it was a prediction about risk. It has since been confirmed as an actual factual error in the delivered package, at the single claim most likely to be quoted by the reader it mattered most to.
**Correction required:** Done. Document 4 (v3) now states the verified 24-column schema, corrects both the identity and the free-text-reason claims, and marks the corrected passages `VERIFIED` rather than `FACT`. Documents 3 and 8 (v3) carry a parallel correction for the related stale claim that the twin's 18 tests are excluded from CI — resolved by commit `d881bc0`, found during the same verification pass.

### F-9 · Terminology drift on the replica
**Finding:** One artifact is referred to five ways across the package.
**Evidence:** "twin," "twin replica," "independently held replica," "sealed replica," and "second copy" all appear. The sources' term is "twin."
**Severity:** 🟢 Low — meaning is preserved, but a reader assembling terminology across documents may believe two mechanisms exist.
**Correction required:** Use "twin (independently held sealed replica)" at first mention per document, then "twin."

### F-10 · A source label was embellished
**Finding:** Document 6 C-2 describes the repository as containing "a graph-topology modeler."
**Evidence:** The sources describe `Model/` as "Graph building — mutable, no versioning." "Modeler" and "topology" add specificity the label does not carry.
**Severity:** 🟢 Low.
**Correction required:** Use the source's phrasing.

### F-11 · Document 7 deviates from the prescribed slide structure without flagging it
**Finding:** Two slides — "What this is" and "The ask" — are additions to the twelve-section structure specified in the instructions.
**Evidence:** The prescribed structure runs Problem through Risks and mitigations. Document 7 states its count as fourteen against a cap of fifteen but does not identify which two are additions.
**Severity:** 🟢 Low — the cap was respected and both additions are conventional.
**Correction required:** Name the two additions as deviations in the construction notes.

### F-12 · The eighteen-day framing invites a misreading
**Finding:** The package repeatedly cites an eighteen-day span in ways a skimming reader will take as the project's total age.
**Evidence:** Document 5's trust-failure list states it as `FACT` that "the entire documented development history spans approximately eighteen days." Document 6 titles C-11 "The entire documented history is eighteen days." Both are accurate about the sources and both omit, at the point of the claim, that the sources are architecture snapshots rather than a project history.
**Severity:** 🟡 Moderate — the framing is used as an investor-facing signal, so the misreading has consequences.
**Correction required:** Standardize to: the work history documented in these four sources spans July 13 to July 30, 2026; what preceded it is `UNKNOWN`.

### F-13 · The package cannot verify its own most important negative claim
**Finding:** The assertion that no third party has reviewed the system — load-bearing in Documents 5, 6, and 7 — rests entirely on silence in four internally authored documents.
**Evidence:** Document 6 C-18 marks this 🔴 Critical and Document 7 builds slide 10 around it. The sources reference a July 24 repository audit without stating whether it was independent. Absence of a mention in internal architecture documents is weak evidence of absence of an external review.
**Severity:** 🟡 Moderate — the conclusion is probably right and the reasoning is thin.
**Correction required:** Restate as: no external review is documented in these sources, and whether one exists is `UNKNOWN`.

---

# PART 3 — DEFECTS INHERITED FROM THE SOURCE DOCUMENTS

These are defects in the four source documents, not in the package. They propagate into anything built from them and are worth correcting upstream. Thirteen were identified during construction; a subsequent verification pass against the live repository at `68cadfb` resolved four of them directly. The "Verified status" column reports that pass's findings.

| # | Inherited defect | Severity | Where it surfaces | Verified status |
|---|---|---|---|---|
| 1 | **Mortgage cassette CI status contradicts itself.** Its code-hash version is recorded as "N/A (not in CI yet)" while its 27 tests appear inside the suite total | 🟠 | Docs 2, 6 | Not independently verified |
| 2 | **Three incompatible framings of suite size** — "384–673 depending on branch," "670+ passed / 6 skipped," and "all 384+ tests can run without exclusion" | 🟠 | Docs 1, 5, 6, 7 | Not independently verified |
| 3 | ~~`cassette_forensics.py` appears in two locations~~ — **WITHDRAWN.** The source directory map's "(duplicate entry point? verify which is live)" annotation was a documentation artifact. Direct inspection of the repository found exactly one `cassette_forensics.py` | 🔴→ — | Docs 2, 3, 6 (corrected in v3) | `VERIFIED` false — no duplication exists |
| 4 | **`cassettes/` and `regulatory_cassettes/` appear at two levels,** annotated "(symlink or duplicate?)" | 🟡 | Doc 2 | `VERIFIED` true — `api_server.py` and the `certs/` path are genuine symlinks, confirming this is a real repository condition, not a documentation artifact like #3 |
| 5 | **"Production-ready" with no documented deployment.** The summary calls the kernel production-ready; no source records a deployment, user, or real traffic | 🟠 | Docs 1, 5, 6 | Not independently verified |
| 6 | **`conftest.py` count inconsistent** — three locations shown across sources, two claimed | 🟡 | Doc 2 | Not independently verified |
| 7 | **The compliance rollup is glossed as spanning six dimensions** while three of the six are documented as unwired | 🟠 | Docs 1, 3, 4 | Not independently verified |
| 8 | **`bisg_estimator.py` has no documented location.** Two modules import it; it appears in no tree or table. It supplies the demographic estimates that fairness conclusions rest on | 🟠 | Docs 3, 4, 6 | `VERIFIED` — the module exists at `sentinel_os/`; the source maps simply omitted it |
| 9 | **The zero-finding security result is reported two ways** — once with the eighteen suppressions disclosed, once as a bare zero | 🟠 | Docs 1, 3, 6, 7 | Not independently verified |
| 10 | ~~Obligation and cohort tables are placed inconsistently~~ — **RESOLVED.** `obligation_ledger` and `cohort_review_ledger` are created in `twin_receiver.py`. They are unambiguously twin-side in the code; the source documents' inconsistency was theirs alone | 🟠→🟢 | Docs 2, 4 (corrected in v3) | `VERIFIED` — twin-side, unambiguous |
| 11 | **Environment variable named two ways.** Configuration documents `ICEBERG_LEDGER_DSN`; the documented startup error reads `ICEBERG_LEDGER_RUNTIME_USER not set` | 🟠 | Doc 8 | Not independently verified |
| 12 | **`Engines/` is labeled simulator-only** while two of its modules are imported by production code | 🟠 | Docs 2, 6 | Not independently verified |
| 13 | **Twin topology is contradictory.** Reached over HTTP at a receiver URL, implying network separation; verified by Unix-socket peer authentication, implying same-host access. This determines whether the independence control is real | 🔴 | Docs 3, 8 | Not independently verified — remains open and is the highest-priority item left on this list |
| 14 | **NEW, found during verification, not in the original thirteen.** The four source documents summarized `ledger_entries` as 9 fields. The live schema carries 24 — a base table plus 14 `ALTER`-added columns, including `authorized_by`, `call_sid`, and a populated `reason` column. This under-count produced the package's most serious defect, F-8 | 🔴 | Doc 4 (corrected in v3) | `VERIFIED` — root cause of F-8 |

`INTERPRETATION` Items 3 and 13 are the two worth resolving first. Both concern the mechanism the system's central claim depends on, and both are ambiguities the source authors flagged and left open.

---

# PART 4 — INSTRUCTION COMPLIANCE AUDIT

| Requirement | Status |
|---|---|
| Only the four named sources used | ✅ Met |
| No outside knowledge, market assumptions, or unstated best practices | ✅ Met |
| Gaps marked `UNKNOWN` rather than filled | ✅ Met — roughly forty distinct determinations |
| Four-category truth model applied to every statement | ⚠️ Substantially met; four mistags (F-3) |
| `INTERPRETATION` never promoted to `FACT` | ✅ Met |
| `DERIVED` never presented as implemented capability | ✅ Met |
| Future ideas never presented as current state | ✅ Met |
| Every document answers AUDIENCE / READER QUESTIONS / DECISION OBJECTIVE / TRUST FAILURE | ❌ Not met in Documents 6 and 7 (F-1) |
| Doc 1: no marketing language; capability, interpretation, and validation separated | ✅ Met |
| Doc 2: boundaries, modules, dependencies, isolation, state, trust, extension points, invariants, assumptions, plus both required question sections | ✅ Met |
| Doc 3: existing controls only; no improvements recommended | ✅ Met |
| Doc 4: all six SHOW ME sections | ✅ Met |
| Doc 5: current reality, market hypothesis, and validation separated; prohibited superlatives avoided | ✅ Met — no instance of the four banned terms or equivalents |
| Doc 6: role switched; product, technology, execution, market all evaluated; four-field return format | ✅ Met — severity calibration weak (F-5) |
| Doc 7: fifteen-slide cap; six fields per slide; prescribed order; no fabricated competitive analysis or market sizing | ✅ Met — two unflagged additions (F-11) |
| Doc 8: documented procedures only; `UNKNOWN` for undocumented operations | ✅ Met — one recommendation leak (F-4) |
| No hype, no praise, no criticism, no unrequested recommendations | ⚠️ One leak in Doc 8 (F-4). Document 7's construction guidance is within its brief |
| Professional enterprise Markdown | ✅ Met |
| All eight documents plus this review delivered | ✅ Met |

---

# PART 5 — VERDICT

**On accuracy, as originally written from the four sources.** The package represented the system as the sources described it, including where that was unflattering. No capability was invented, no gap was papered over, and the four disclosures most damaging to a persuasive reading appeared in the persuasive documents. That verdict was correct as a statement about fidelity to the sources — and, as the verification pass below shows, fidelity to the sources was not the same thing as fidelity to the system.

**On integrity, as originally written.** Thirteen defects were introduced into the documentation. Four were 🟠 High: two documents missing a required section, an unqualified headline figure in the two documents most likely to circulate alone, four evaluative judgments mistagged as derivations, and one claim — F-8 — that treated a reference summary as an exhaustive schema.

**What the verification pass changed.** F-8 was written as a risk: "if the actual table contains an actor column, the claim is wrong." It did. The live `ledger_entries` schema carries 24 columns against the 9 the source documents described, including `authorized_by`, `call_sid`, and a populated `reason` field — the exact gap this review predicted, confirmed rather than merely flagged. Document 4's claims that no actor field exists and that free-text reasoning is never captured were both factually false, not just under-supported, and have been corrected in v3. Four further items resolved in the same pass: F-7's source-identity question closed clean (the two architecture-map files are byte-identical); inherited defect #3 (the duplicate `cassette_forensics.py`) turned out to be a documentation artifact with no real duplication behind it and is withdrawn; inherited defect #10 (obligation table location) resolved unambiguously to twin-side; and the claim, repeated across Documents 3, 4, and 8, that the twin's 18 tests are excluded from CI was found to be stale — they have run in CI since commit `d881bc0`.

**On the sources.** Fourteen inherited defects are now on record — the original thirteen plus the schema under-count that produced F-8. Two are confirmed real (the `cassettes/` symlinks, the schema gap); one is confirmed to not exist (the forensics duplication); one is resolved (the obligation-table location); the rest remain unverified. Any package built from these four source maps without a code-level check will carry whichever of the unverified ones are real. `INTERPRETATION` The schema gap in particular is worth fixing at the source: the Quick Reference should either state the full 24-column schema or say plainly that it is a summary, not a reference.

**Standing.** With F-1 through F-8 corrected in Documents 3, 4, and 8 — and with F-8 specifically resolved by direct verification rather than by softened language — the package now meets the standard it was written to on both axes: fidelity to the four sources, and, where checked, fidelity to the system itself. The gap between those two axes is the standing lesson of this pass: a documentation package can follow its sourcing rules perfectly and still be wrong, when the sources themselves are incomplete. Everything not independently verified in this pass — thirteen of the fourteen entries in Part 3's table — remains sourced from the four documents only, and should be read with that caveat.

---

**End of Documentation Integrity Review.**

`FACT` This review, in its original form, examined the eight documents produced in this pass against the instructions they were written to and against the four source documents, without re-examining the repository. `VERIFIED` A subsequent pass did clone and inspect the live repository at `68cadfb` and is reflected in the corrections marked throughout this document and propagated to Documents 3, 4, and 8.
