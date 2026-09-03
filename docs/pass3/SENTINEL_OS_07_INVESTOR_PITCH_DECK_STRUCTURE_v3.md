> **⚠ Point-in-time snapshot — not maintained.** Documented baseline `68cadfb` (July 29, 2026). Much has landed since — PRs #28–#45 as of 2026-09-03: the keyed `authorized_by` ledger attestation with key rotation, the persisted observed-event layer, the **extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo** (2026-08-28) — its standalone simulator, `Domain/` `Sim/` `Engines/` `Model/` `observe/` tree, Twilio ingestion, Claude governor client, and queue/staffing/Bayes layer are no longer in this repo — the mandatory `conservation/` boundary, and a widened CI gate set. Treat every directory map, module inventory, test count, and CI description below as historical. For current state see the [repository root README](../../README.md) and **[POST_SNAPSHOT_CORRECTIONS_2026-09-03.md](./POST_SNAPSHOT_CORRECTIONS_2026-09-03.md)**, which reconciles the specific quantitative claims (test totals, the security-scan framing, the mortgage-cassette-in-CI question) a present-day reader would otherwise take as current.

---

# DOCUMENT 7 — INVESTOR PITCH DECK STRUCTURE

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification:** `FACT` = stated in a source document · `DERIVED` = follows from two or more documented facts · `INTERPRETATION` = reasonable reading, not established · `UNKNOWN` = not in the sources.

**Constraint honored.** No competitive analysis and no market sizing is fabricated. Two slides in the prescribed structure cannot be built from existing material for exactly that reason, and they are marked as such rather than filled with invented content.

**Slide count:** 14, against a cap of 15.

---

## BUILDABILITY SUMMARY — READ THIS FIRST

Legend: 🟢 buildable today from documented material · 🟡 buildable with a disclosed gap · 🔴 cannot be built without new evidence

| # | Slide | Status | Blocking gap |
|---|---|---|---|
| 1 | What this is | 🟡 | No written product definition exists |
| 2 | Problem | 🟡 | No practitioner has stated the problem in their own words |
| 3 | Current landscape | 🔴 | **No competitive or landscape information exists in any source** |
| 4 | Why existing approaches may fail | 🟢 | — |
| 5 | The Sentinel OS approach | 🟢 | — |
| 6 | Architecture advantage | 🟢 | — |
| 7 | Current implementation | 🟢 | — |
| 8 | Evidence | 🟢 | — |
| 9 | Market opportunity | 🔴 | **No market information of any kind exists in any source** |
| 10 | Validation path | 🟢 | — |
| 11 | Roadmap | 🟡 | Items documented; no dates or resourcing |
| 12 | Investment thesis | 🟡 | The "why now" has no external support |
| 13 | Risks and mitigations | 🟢 | — |
| 14 | The ask | 🟡 | No amount, terms, or use of funds exists |

`DERIVED` Six slides are fully supportable today. Six carry a disclosed gap. Two cannot be built at all without primary research, and both are the market-facing slides — which is where a deck of this kind is most likely to invent something and most likely to be caught doing it.

---

## SLIDE 1 — WHAT THIS IS

**Slide purpose:** Prevent miscategorization in the first fifteen seconds. This is audit infrastructure, not AI tooling, and the two have different buyers and different tolerance for immaturity.

**Investor question answered:** What am I looking at?

**Main message:** A record-of-decision layer for automated decisions — infrastructure that makes an AI decision examinable by a party who does not trust the system that made it.

**Supporting evidence:** `FACT` A governance kernel with an append-only hash-chained ledger, an independently held sealed replica, provenance stamping on every observation, and outcome tracking past the decision point. Roughly 670 tests passing, continuous integration green.

**Missing proof:** `FACT` No mission statement, charter, or product definition exists in any source document — the internal architecture map says so explicitly. This slide states a category the code implies rather than a purpose the builder has committed to in writing.

**Suggested visual:** One sentence, centered. Below it, four words naming the category. Nothing else.

---

## SLIDE 2 — PROBLEM

**Slide purpose:** Establish that the problem exists independently of the product, so the rest of the deck is not solving for its own architecture.

**Investor question answered:** Is this a real problem, or one invented to fit a build?

**Main message:** An organization running automated decisions often cannot reconstruct why one was made — to a reader who assumes the organization has an incentive to shade the answer. Better models do not reduce that exposure.

**Supporting evidence:** `DERIVED` The problem is legible from what the system refuses to do rather than from any claim: history cannot be rewritten, a second party holds the copy, the acting system's account of itself is distrusted, and every claim is labeled by how it is known. `FACT` The first application is consumer lending, where explaining an adverse decision is a legal obligation — the reference lens implements CFPB Reg B and the statistical test is identified as a CFPB / ECOA standard.

**Missing proof:** `UNKNOWN` No compliance officer, examiner, auditor, or institution is on record anywhere in the sources describing a request they cannot answer today. Without one practitioner voice, this slide is an argument rather than a finding.

**Suggested visual:** A single question — "why was this application declined?" — and beside it the artifacts a conventional stack produces in response: an application log, a model version, a score. Let the gap be the visual.

---

## SLIDE 3 — CURRENT LANDSCAPE 🔴

**Slide purpose:** Situate the system among existing approaches.

**Investor question answered:** Who else does this, and if it matters, why hasn't it been solved?

**Main message:** `UNKNOWN` Cannot be stated. The source documents contain no competitive information, no category analysis, no analyst reference, and no description of how institutions meet these obligations today.

**Supporting evidence:** `FACT` The only landscape facts available are the standards the system implements against: CFPB Reg B, the four-fifths rule as a CFPB / ECOA standard, and a compliance baseline document in the repository. `FACT` Those standards already exist and are already being met somehow by regulated institutions. How well, at what cost, and with what tooling is not documented anywhere.

**Missing proof:** The entire slide. This is the most dangerous slide in the deck — it is where a founder is most tempted to construct a competitive grid from memory, and where a technical investor is most likely to check a claim and find it invented.

**Suggested visual:** Do not build this slide from current material. If it must appear before research exists, present it as an explicit open question — "how these obligations are met today, and at what cost, is what we are researching" — and let the honesty do the work. An empty labeled axis is survivable; a fabricated quadrant is not.

---

## SLIDE 4 — WHY EXISTING APPROACHES MAY FAIL

**Slide purpose:** Explain why this cannot simply be a feature added to something the buyer already owns.

**Investor question answered:** Why isn't this a two-week project for an incumbent?

**Main message:** An audit log the organization can edit is not evidence. Three properties are structurally hard to add after the fact.

**Supporting evidence:**
`FACT` **Enforcement below the application.** Immutability is a database trigger; the runtime identity holds no UPDATE or DELETE and refuses to start if it is the table owner or a superuser; the second copy sits under a different database role and a different operating-system identity. An application compromise does not by itself grant the ability to rewrite history.
`FACT` **Typed uncertainty on every write path.** Three provenance stamps that are explicitly not interchangeable, INDETERMINATE as a first-class result rather than a default pass, and a rule requiring an unknown to record why it is unknown and what would close it. This constrains every write rather than sitting at the edge.
`FACT` **Resolution conditions fixed before outcomes are known.** Maturation rules are declared and hashed at decision time, which makes testable whether the definition of success changed after the result arrived.

**Missing proof:** `UNKNOWN` No organization is documented as having attempted this retrofit and stopped. `INTERPRETATION` The honest position is that each mechanism is individually replicable by a competent team; the argument is about sequence and cost, not about impossibility.

**Suggested visual:** Three rows. Column one, the property. Column two, retrofittable or not. Column three, one line on why.

---

## SLIDE 5 — THE SENTINEL OS APPROACH

**Slide purpose:** Convey the whole system in one diagram so the architecture slide has something to build on.

**Investor question answered:** What did you actually build?

**Main message:** A core that knows nothing about any industry, industry knowledge in swappable modules, and regulatory rules as read-only overlays that can raise findings but cannot alter a decision.

**Supporting evidence:** `FACT` The kernel imports no domain code, and a module declaring a setting belonging to a capability it has not enabled is refused at load time. `FACT` Three domain modules exist — call center as the reference implementation, banking, and mortgage. `FACT` Six compliance dimensions and one reference regulatory lens. `FACT` The mortgage module declares only the outcome-obligation capability, demonstrating that a module can be minimal.

**Missing proof:** `FACT` Only one of the three modules exercises outcome tracking, and all three were written by the same person. The reusability claim — the load-bearing claim of the whole architecture — has not been tested by an outside author.

**Suggested visual:** Kernel at center. Three interchangeable modules docking into it, one shown as a thin slice to make the minimal-module point. Lenses as translucent overlays. The replica as a separate box, visually outside the main boundary, labeled with its separate identity.

---

## SLIDE 6 — ARCHITECTURE ADVANTAGE

**Slide purpose:** Name what is structurally distinctive without overclaiming defensibility.

**Investor question answered:** What is genuinely hard here?

**Main message:** The distinctive property is a set of refusals. The system will not let its own verdict act, will not treat an estimate as a measurement, will not accept an undeclared parameter, will not report a pass on a check it did not run, and will not start over-privileged.

**Supporting evidence:** `FACT` Ten decisions are recorded as locked, each traced to a named enforcement point — including that the judge verdict does not drive behavior while a separate score retains routing control, that disclosure must be written before any effect, and that regulatory lenses make zero outside calls during judgment.

**Missing proof:** `FACT` None of these mechanisms is exclusive, patented, or proprietary; the stack is Python over PostgreSQL and Redis with standard cryptography. `INTERPRETATION` What is scarce is arriving at this particular set of refusals, which is a head start and a coherent design rather than a moat. Say this on the slide. An investor who reaches it first stops trusting the rest.

**Suggested visual:** Five lines of text, one per refusal, each with its enforcement point in small type beside it. No architecture diagram — slide 5 already carried that load.

---

## SLIDE 7 — CURRENT IMPLEMENTATION

**Slide purpose:** Separate what is built from what is built-but-unconnected from what is a placeholder, before an investor does it less generously.

**Investor question answered:** What is real today?

**Main message:** The evidentiary core is built and tested. The layer that would feed it real-world data is defined by contract and not connected.

**Supporting evidence:**
`FACT` **Built and tested:** append-only ledger, hash chaining, on-demand chain verification, independently held replica, provenance stamping, outcome obligations with declared maturation rules, three domain modules, code-hash tamper detection, fail-closed startup, three of six compliance dimensions active by default.
`FACT` **Built, not connected:** the geographic-equity dimension, two opt-in dimensions, per-decision compliance evaluation, and three prepared patches awaiting application.
`FACT` **Placeholder:** live call ingestion returns an empty list; the AI-explanation executor returns a hardcoded value; reinforcement learning was removed and is on hold.

**Missing proof:** `UNKNOWN` No deployment, operator, user, or production run appears in any source. `FACT` Every green result is green against fixtures.

**Suggested visual:** Three columns with honest counts at the top of each. Do not use a progress bar — a percentage implies a denominator the sources do not support.

---

## SLIDE 8 — EVIDENCE

**Slide purpose:** Present the verification record with its qualifications visible on the same slide.

**Investor question answered:** How do I know any of this works?

**Main message:** Roughly 670 tests pass and 6 skip, confirmed twice back to back; lint clean on a pinned version; continuous integration first green July 24 with the last five recorded runs green. Four qualifications belong on this slide, not in a footnote.

**Supporting evidence:**
`FACT` The suite is elsewhere described as ranging 384 to 673 tests depending on branch.
`VERIFIED` (corrected in v3) This qualification originally stated that the 18 tests covering the independently held replica are excluded from continuous integration. That is stale — commit `d881bc0` added native database and OS-identity provisioning to the CI job specifically so these tests could run there, and they now do, on every commit. Drop this line from the slide; the three remaining qualifications (the branch-dependent range, the flaky test, the eighteen justified security suppressions) still stand.
`FACT` One test is known-flaky under load.
`FACT` The zero-finding security scan result was reached by annotating and justifying one high-severity and seventeen medium-severity findings.

**Missing proof:** `UNKNOWN` No third party — auditor, examiner, security assessor, or certification body — is documented as having reviewed anything. `FACT` The sources reference a July 24 repository audit, and do not state whether it was independent.

**Suggested visual:** The headline number large, with four visible asterisks resolving to four short lines directly beneath it. `INTERPRETATION` A skeptic who discovers these himself discounts the entire deck; a founder who displays them buys credibility for every other number. This is the highest-leverage design choice in the deck.

---

## SLIDE 9 — MARKET OPPORTUNITY 🔴

**Slide purpose:** Establish the size and shape of the opportunity.

**Investor question answered:** How big can this be?

**Main message:** `UNKNOWN` Cannot be stated from existing material. The segment is definable — regulated institutions making automated consumer-credit decisions subject to adverse-action explanation obligations — but no size, growth rate, budget, or price point exists anywhere in the sources.

**Supporting evidence:** `FACT` The only market-adjacent facts available are directional: the reference lens implements a consumer-lending regulation, the statistical test is a lending standard, and the first outcome-tracking module is mortgage lending. That indicates a chosen entry point, not a measured market.

**Missing proof:** All of it. `UNKNOWN` No sizing, no analyst data, no procurement data, no budget benchmarks, no pricing, no comparable transactions, and no customer conversations appear in any of the four source documents.

**Suggested visual:** None. `INTERPRETATION` A deck of thirteen slides with no market slide is a recoverable position — it reads as early and honest. A deck with one invented number is not, because the number is checkable and its discovery retroactively discredits slides 4 through 8, which are the strong ones. If a market slide is required, present the segment definition and the research method, with no figures.

---

## SLIDE 10 — VALIDATION PATH

**Slide purpose:** Demonstrate that the builder knows precisely what would falsify or confirm the thesis.

**Investor question answered:** What would convince me, and does he know?

**Main message:** Four actions, none of which requires scale, capital, or infrastructure.

**Supporting evidence:** `DERIVED` Each maps to a gap the sources themselves identify:
1. **Connect one real event source.** Converts every green result from fixture-tested to data-tested. The ingestion contract is already defined and covered by 19 tests.
2. **Obtain one examiner's written view of the ledger output.** The only thing that can validate the core premise. Requires building nothing.
3. **Add a second engineer with commit rights.** Removes the single-approver bottleneck and the key-person dependency simultaneously. Three prepared patches currently await one person.
4. **Have an outside author write one domain module.** Tests the reusability claim the architecture depends on.

**Missing proof:** `FACT` None of the four has been done.

**Suggested visual:** Four rows. Action, the specific doubt it removes, and whether it is done. All four "not yet" — shown rather than hidden.

---

## SLIDE 11 — ROADMAP

**Slide purpose:** Show near-term sequence drawn from the documented open-item list rather than from aspiration.

**Investor question answered:** What happens next, and is it credible?

**Main message:** The near-term work is already enumerated in the system's own limitations and outstanding-items tables. Nothing on this slide is invented.

**Supporting evidence:** `FACT` Apply three prepared patches — geographic equity checking, abandon-on-modification orchestration, and sweep scheduling. `FACT` Wire the geographic dimension into the live path. `VERIFIED` (corrected in v3) This item originally listed getting the replica's 18 tests into continuous integration as outstanding work. It is already done, per commit `d881bc0`, prior to this correction. `FACT` Connect a real event source; the contract is ready and the vendor choice is documented as pending. `VERIFIED` (corrected in v3) This item originally listed three structural ambiguities as unresolved. Direct verification found: the governance module was never actually duplicated — one copy exists; the "unlocated module" (`bisg_estimator.py`) exists at `sentinel_os/` and was simply omitted from the source maps; the two possibly-duplicate directories are confirmed real, genuine symlinks. Only the twin's HTTP-versus-Unix-socket topology question, raised elsewhere in the package, remains genuinely open. `FACT` Deliberately excluded, with reasons recorded: additional loan-type modules are cancelled, reinforcement-learning training is on hold, and the AI-explanation executor remains stubbed.

**Missing proof:** `UNKNOWN` No dates, no sequencing commitments, no resourcing assumptions, and no roadmap beyond these documented items exist in any source.

**Suggested visual:** A list, not a timeline. Every item annotated "documented open item" to make the provenance visible. `INTERPRETATION` A timeline implies dates the sources cannot support, and a fabricated date is the second-easiest thing for an investor to test after a market number.

---

## SLIDE 12 — INVESTMENT THESIS

**Slide purpose:** State the bet as a bet.

**Investor question answered:** Why this, why now, why him?

**Main message:** The bet is that obligations to produce evidence about automated decisions harden, and that retrofitting evidence onto systems not built for it proves harder than adopting a purpose-built layer. `INTERPRETATION` This is a hypothesis, and labeling it as one is the correct move with an investor who has heard the alternative.

**Supporting evidence:** `FACT` The asset is a coherent evidentiary core, built and tested, plus a documentation practice that names its own failures in the same voice as its capabilities — that redlining is not prevented in real decisions, that a removed module was never real training, that a zero-finding scan required eighteen justified suppressions, that a heuristic is a heuristic. `INTERPRETATION` In a control product, that trait is not adjacent to the product. It is the product.

**Missing proof:** `UNKNOWN` The "why now" has no external support in any source — no regulatory timeline, no enforcement trend, no demand signal. `FACT` The "why him" rests on one person, with no team, no second reviewer, and eighteen days of documented history.

**Suggested visual:** The bet in one sentence at the top. Beneath it, in equal weight, the three conditions that would falsify it: obligations remain satisfiable by ordinary logging; platforms buyers already own add sufficient provenance; institutions conclude that producing estimated-demographic fairness records worsens their legal position.

---

## SLIDE 13 — RISKS AND MITIGATIONS

**Slide purpose:** State the strongest objections before the investor does, at their real severity.

**Investor question answered:** What kills this, and does he see it?

**Main message:** Four objections are critical, and three of the four are cheap to close.

**Supporting evidence:**

| Risk | Severity | Closing action | Status |
|---|---|---|---|
| No stated product definition; no named buyer | 🔴 | Write it; then five conversations with regulated institutions | Not started |
| One person, no second reviewer, on a product about independent verification | 🔴 | Second engineer with commit rights and a review requirement | Not started |
| Never run on real data | 🔴 | Connect one live event source | Contract ready, source not connected |
| No third-party validation at any level | 🔴 | One examiner's written view | Not started |
| Buyer's counsel may decline to create estimated-demographic disparate-impact records that cannot be deleted | 🟠 | An outside-counsel opinion at a regulated institution | Not started |
| Reusability thesis rests on three modules by one author | 🟠 | One module by an outside author | Not started |
| Mechanism is replicable by a competent team | 🟠 | No technical resolution; depends on adoption and standards position | Unresolved |

**Missing proof:** `FACT` Every mitigation above is unstarted. `INTERPRETATION` Presenting them as unstarted is stronger than presenting them as in progress, because slide 10 has already established that the builder can name them precisely — and an investor trusts a named gap more than a claimed remedy.

**Suggested visual:** The table above, severity colors intact. Do not soften the four criticals into "considerations."

---

## SLIDE 14 — THE ASK

**Slide purpose:** Connect resources to specific evidence rather than to a runway figure.

**Investor question answered:** What do you want, and what do I get for it?

**Main message:** The binding constraints are four: one additional engineer, one compliance professional's review, one examiner conversation, and one connected data source. None requires scale or infrastructure spend.

**Supporting evidence:** `DERIVED` Each constraint maps to a documented gap and to a specific concern it closes — the single-approver bottleneck with three patches queued behind it, an unvalidated regulatory lens, an unvalidated core premise, and a fixture-only verification record.

**Missing proof:** `UNKNOWN` No amount, terms, valuation, use of funds, hiring plan, or timeline exists in any source. This slide cannot carry a number today.

**Suggested visual:** Three columns — resource, the doubt it removes, the artifact it produces. `INTERPRETATION` An investor at this stage is buying evidence, not runway. Framing the ask as a purchase of specific proofs is both more honest and more persuasive than a burn-rate chart the sources cannot support.

---

## CONSTRUCTION NOTES

`INTERPRETATION` Three notes about the deck as a whole.

**The strong slides are 4 through 8 and 10.** They are fully supportable from documented material and they carry the argument. `DERIVED` A deck built from only those six, plus a title and a risk slide, is defensible today. Every additional slide is currently weaker than the ones it sits beside.

**The two market slides are the failure mode.** Slides 3 and 9 cannot be built from existing material. `INTERPRETATION` Their absence reads as early-stage; their fabrication discredits the slides that are real. The asymmetry is severe and one-directional.

**Slide 8 is the credibility hinge.** `FACT` Four qualifications attach to the headline verification number, all of them discoverable within minutes by anyone reading the source documents. `INTERPRETATION` Whether they appear on the slide or are found by the reader determines how the previous seven slides are received.

---

**End of Document 7.**

`FACT` Grounded solely in the four source documents at repository state `68cadfb`. No competitive grid, market size, growth rate, price point, or customer reference appears here, because none exists in the sources.
