# DOCUMENT 5 — INVESTOR SYSTEM BRIEF

**System:** Sentinel OS
**Repository:** `github.com/wking53214/sentinel_os`
**Documented baseline:** `origin/main` at commit `68cadfb`, July 29, 2026
**Documentation pass:** Pass 3, Round 2
**Source authority:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`, `SENTINEL_OS_TECHNICAL_ARCHITECTURE_MAP.md`

**Classification:** `FACT` = stated in a source document · `DERIVED` = follows from two or more documented facts · `INTERPRETATION` = reasonable reading, not established · `UNKNOWN` = not in the sources.

**What this document is.** A first-pass assessment written from the position of an investor who has been given access to a repository and four internal architecture documents, and nothing else. There is no pitch, no deck, no data room, no founder narrative, and no financials in the source material. This document does not advocate. It establishes what is real, what the architecture implies, and what would have to be true for the implication to matter.

**Constraint honored throughout.** The source documents contain no market sizing, no competitive information, no customer information, no pricing, and no commercial history of any kind. None is supplied here. Every market statement below is labeled as hypothesis.

---

## 0. READER FRAME

**AUDIENCE**
Early-stage technology investor, or a technical diligence lead supporting one.

**READER QUESTIONS**
1. Is this a product, a platform, or an engineering exercise?
2. Is the thing that exists actually built, or described?
3. What is hard here, and would a well-resourced team find it hard?
4. Who buys it, and is there any evidence anyone wants it?
5. What is the single fact that would end my interest?
6. If I spent a week on diligence, what would I be trying to learn?

**DECISION OBJECTIVE**
Decide whether to commit diligence time — not capital. This document is deliberately insufficient for an investment decision, and the reason is stated in §7: the source material contains no commercial evidence at all.

**TRUST FAILURE**
This reader disengages, correctly, if the following are not surfaced immediately rather than buried:

- `FACT` No customer, user, pilot, letter of intent, revenue, pricing model, or commercial conversation appears anywhere in the four source documents.
- `FACT` No source establishes that the system has processed real production data. The primary external data source is an unimplemented stub returning an empty list.
- `FACT` One named individual is the only human associated with the project anywhere in the sources.
- `FACT` The entire documented development history spans approximately eighteen days, from July 13 to July 30, 2026.
- `FACT` No patent, proprietary dataset, trade secret, or exclusive relationship is documented. The implementation is Python over PostgreSQL and Redis.

---

## 1. CURRENT REALITY

Only what the sources establish as existing.

**What is built and tested**

`FACT` A governance kernel of eight modules that is domain-blind — it imports no domain-specific code.
`FACT` An append-only decision ledger in PostgreSQL 16, with immutability enforced by a database trigger rather than by application code, and records hash-chained on `current_hash` / `previous_hash`.
`FACT` An independently held twin replica under a separate database role and separate operating-system identity, using a sealed envelope (X25519 + AES-GCM), with chain integrity recomputed independently at three sites that must agree.
`FACT` A provenance system stamping every observation VERIFIED, ATTESTED, or ESTIMATED, with a governing rule requiring an unknown to record why it is unknown and what would close it.
`FACT` An outcome-obligation system tracking decisions past the decision point — OPEN, RESOLVED, or ABANDONED — with a resolution condition declared and hashed at decision time. The documented example carries a three-year horizon.
`FACT` Three domain cassettes on one interface: call center (reference implementation), banking, and mortgage. The mortgage cassette is the first to implement outcome obligations, declaring that capability only.
`FACT` A regulatory lens framework with six compliance dimensions and one reference lens implementing CFPB Reg B, including a four-fifths-rule disparate-impact test.
`FACT` Tamper detection on decision logic itself: code hashes bound at write time, with a version-conflict error on mismatch and fail-closed binding enforcement.
`FACT` Roughly 670 tests passing with 6 skipped, confirmed twice back to back. Zero lint findings on a pinned version. Zero security-scan findings at medium and above. Continuous integration first fully green July 24, with the last five recorded runs green.

**What is built and not connected**

`FACT` The geographic-equity dimension is implemented but not wired to the live path; its patch is prepared and unapplied.
`FACT` Two of six dimensions are opt-in and off unless configured. Three are active by default.
`FACT` Per-decision compliance evaluation exists as a function that is not called from the live decision path.
`FACT` Three prepared patches await application by the single documented individual.
`FACT` The event-ingestion contract is defined and covered by 19 tests; no real event source is connected.

**What is absent or stubbed**

`FACT` Live call ingestion returns an empty list.
`FACT` The AI-explanation fallback executor returns a hardcoded value.
`FACT` Reinforcement learning: the untrained module was removed and real training is explicitly on hold.
`FACT` No interface specification, no static type enforcement, no documented authentication for the HTTP API, no user interface of any kind.
`UNKNOWN` No deployment, operator, retention policy, backup procedure, encryption at rest, or monitoring is described.

**What the timeline shows**

`FACT` The documented work history runs from July 13 to July 30, 2026 — roughly eighteen days — and includes governance hardening, a kernel and capability split, a regulatory cassette framework, six compliance dimensions, an event and outcome framework, cohort assembly, a mortgage cassette, CI repair, and two dead-code sweeps.

`INTERPRETATION` Two readings are available and diligence must choose between them. Either this represents unusual individual velocity on a coherent design, or a test count and module inventory are being read as maturity when the system is roughly three weeks old. The sources support both readings and settle neither. The question is answerable in a single conversation with the builder about what preceded July 13.

`FACT` The sources reference a July 24 audit of the repository. `UNKNOWN` Whether that audit was independent or self-performed.

---

## 2. SYSTEM CATEGORY

`FACT` The sources describe what exists as "a production-ready governance kernel with tamper-evident dual-ledger."
`FACT` No source contains a mission statement, product definition, or charter. The internal architecture map states this explicitly and declines to supply one.

`DERIVED` Functionally the system is a record-of-decision layer: it does not make decisions better, it makes them examinable afterward by a party who does not trust the deciding system.

`INTERPRETATION` The category is closer to audit infrastructure than to AI tooling. That distinction matters commercially, because the two categories have different buyers, different sales cycles, and different tolerance for immaturity.

`UNKNOWN` Whether the builder intends a product, a platform, an internal control, a standard, or a research artifact. This is not a minor gap — it determines what the company would be.

---

## 3. PROBLEM SPACE

`DERIVED` The problem is legible from the invariants rather than from any statement of intent. Four locked decisions define it: history cannot be rewritten; a second party holds an independent copy; the acting system's account of itself is distrusted; and every claim is labeled by how it is known.

`DERIVED` Those four choices only make sense against a specific need — to prove, later, what an automated system decided and on what basis, to a reader who assumes the organization has an incentive to shade the answer.

`FACT` The first concrete application is consumer lending: the reference compliance lens implements CFPB Reg B, the statistical test is the four-fifths rule identified as a CFPB / ECOA standard, and the first outcome-tracking cassette is mortgage lending.

`INTERPRETATION` The problem being addressed is not model quality. It is evidentiary: an organization that cannot reconstruct why its automated system decided something has an exposure that better models do not reduce.

---

## 4. ARCHITECTURAL CHOICES WORTH AN INVESTOR'S ATTENTION

`INTERPRETATION` throughout this section. These are the choices that would distinguish a considered system from an accumulated one.

**Enforcement sits below the application.** `FACT` Immutability is a database trigger; the runtime identity holds no UPDATE or DELETE; custody separation runs through a different database role and operating-system identity. `DERIVED` A compromise of the application does not by itself grant the ability to rewrite history or forge the second copy. Most audit logging cannot make that claim.

**The kernel does not know its industry.** `FACT` The kernel imports no cassette code; a cassette declaring a parameter belonging to a capability it has not enabled is refused at load time. `DERIVED` If the separation holds, adding an industry is a cassette rather than a re-audit of the evidentiary core. `FACT` Three cassettes exist; one exercises the outcome path. `DERIVED` The pattern is demonstrated at small scale and not at scale.

**The governance verdict deliberately controls nothing.** `FACT` The kernel verdict is recorded alongside a quality score that retains routing control. `INTERPRETATION` This is a costly choice — it makes demonstrations less impressive — and a defensible one: a component that never causes an operational outcome cannot be blamed for one. It also signals a builder optimizing for examination rather than for demo.

**Uncertainty is typed, not smoothed.** `FACT` Three provenance stamps that are explicitly not interchangeable; INDETERMINATE as a first-class result rather than a default pass; an unresolved item must record why and what would close it. `INTERPRETATION` This is the design's most distinctive property and the hardest to retrofit, because it constrains every write path rather than sitting at the edge.

**Resolution conditions are fixed before outcomes are known.** `FACT` Maturation rules are declared at decision time and hashed. `DERIVED` This makes testable something normally untestable — whether the definition of success was changed after the result came in.

**Failures happen at load, not at runtime.** `FACT` Four documented refusals occur at startup or cassette load rather than during operation. `INTERPRETATION` A system that refuses to start on misconfiguration is easier to operate safely than one that degrades quietly, with one documented exception: Redis is fail-open.

---

## 5. TECHNICAL SIGNIFICANCE — STATED PLAINLY

`FACT` The implementation is Python over PostgreSQL 16 and Redis 7, using standard cryptographic libraries. No component is documented as novel, proprietary, or patented.

`DERIVED` Nothing in the sources is technically unreachable by a competent team. A hash-chained append-only table, a replica under separate custody, and a plug-in interface are all well-understood constructions.

`INTERPRETATION` What is not commodity is the set of decisions about what the system refuses to do — refusing to let the verdict act, refusing interchangeable confidence levels, refusing implicit parameters, refusing to pass a check it did not run, refusing to start when over-privileged. Those are judgment calls accumulated in a specific direction, and they are the part that is hard to arrive at rather than hard to build. An investor should be clear-eyed that judgment is not a moat in the conventional sense; it is a head start and a coherent design, and its durability depends entirely on execution and adoption rather than on exclusivity.

`UNKNOWN` No defensibility mechanism of any kind is documented: no patent filing, no proprietary data, no exclusive relationship, no network effect, no switching cost, no standards position.

---

## 6. MARKET HYPOTHESIS

Everything in this section is `INTERPRETATION`. The source documents contain no market information whatsoever, and none is invented here.

**The bet the architecture implies.** That obligations to produce evidence about automated decisions will become concrete enough that organizations must satisfy a hostile reader, and that retrofitting such evidence onto systems not designed for it will be harder than adopting a purpose-built layer.

**Why the first vertical appears to be lending.** The reference lens, the statistical test, and the first outcome-tracking cassette all point at consumer credit — a setting where explaining an adverse decision is a legal obligation rather than a preference.

**The structural argument for reusability.** One evidentiary core, many domain cassettes. If it holds, per-vertical cost falls without reopening the audited foundation. The code organization supports the claim; the sources do not demonstrate it beyond one live outcome-tracking domain.

**The unresolved commercial problem, named rather than glossed.** The system's output is consumed by compliance, risk, and external examiners, while its operation belongs to engineering. A product whose beneficiary and whose operator sit in different budgets has an unclear buyer. Nothing in the sources addresses who signs.

**What would falsify the hypothesis.** Evidentiary demand remaining satisfiable by ordinary logging plus attestation; incumbent platforms adding sufficient provenance features; or organizations concluding that estimated demographic inputs make statistical fairness testing more legally risky to produce than to omit.

---

## 7. REMAINING UNKNOWNS

Ordered by how much each would change the assessment.

| # | Unknown | Status |
|---|---|---|
| 1 | Whether any auditor, examiner, regulator, or counsel has reviewed the output and said whether it satisfies them | `UNKNOWN` — the load-bearing question for the entire premise |
| 2 | Whether the system has ever run on real data | `UNKNOWN` |
| 3 | Who the buyer is, and whether any has expressed interest | `UNKNOWN` |
| 4 | Whether a team exists beyond one individual | `UNKNOWN` |
| 5 | What preceded the eighteen days of documented history | `UNKNOWN` |
| 6 | The intended commercial model | `UNKNOWN` |
| 7 | Whether the compliance implementation is defensible to a qualified professional | `UNKNOWN` |
| 8 | Behavior at scale, beyond one known-flaky latency test | `UNKNOWN` |
| 9 | Whether the independence control works in a real deployment | `VERIFIED` (corrected in v3) Its 18 tests now run in CI on every commit, per `d881bc0` — narrower than originally stated. Whether it has been exercised against a real deployment beyond CI remains `UNKNOWN` |
| 10 | Competitive position | `UNKNOWN` — no competitive information exists in the sources |

`DERIVED` Items 1 and 2 are answerable in days and would settle most of the assessment. An investor should not proceed past them.

---

## 8. WHAT ADDITIONAL RESOURCES COULD ENABLE

`DERIVED` The sources identify their own gap list precisely enough to infer what resourcing would address, without speculating about a roadmap. Each item below maps to a documented gap, not to an assumed plan.

| Documented gap | What resourcing would address |
|---|---|
| No live event source; ingestion contract defined and tested | Connecting one real source, which is what converts the system from fixture-tested to data-tested |
| Three patches prepared and unapplied; single approver | A second engineer, which simultaneously removes the review bottleneck and the single-person dependency |
| ~~Twin tests excluded from CI~~ — resolved, `d881bc0` | `VERIFIED` (corrected in v3) No longer open. The twin's tests now run in CI on every commit |
| Dimension 6 unwired; dimensions 2 and 3 opt-in | Full compliance coverage on the live path, which the sources currently state is three of six |
| Reg B lens unvalidated externally | A qualified compliance professional, which is also the cheapest credibility purchase available |
| No examiner has reviewed output | An examiner review, which is the only thing that can validate the core premise |
| One disclosed design-level gap — variable renaming defeats two screens | Research effort; the sources classify this as requiring a new approach rather than a fix |
| `VERIFIED` (corrected in v3) The original list included "decision-time reasoning never captured" as a second gap. This is false: a `reason` field is captured on every decision. The real gap is narrower — what is captured is the deciding AI's self-report, not an independent business reason | An independently supplied business-reason field, which the current schema does not provide |

`INTERPRETATION` Notably, none of the above requires scale, infrastructure spend, or a large team. The binding constraints are one additional engineer, one compliance reviewer, one examiner conversation, and one connected data source.

---

## 9. HOW THE SOURCE MATERIAL ITSELF READS

`INTERPRETATION` One observation about the documents rather than the system, offered because it is diligence-relevant.

`FACT` The source documents disclose their own weaknesses in the same register as their strengths: they state that redlining is not prevented in real decisions, that a removed module was never a real training implementation, that a zero-finding security result was reached by justifying eighteen suppressions, that a heuristic is a heuristic, and that certain structural questions about the repository remain unresolved by the authors themselves.

`INTERPRETATION` Documentation that names its own gaps in the same voice as its capabilities is uncommon, and it is the correct behavior for a control system. It raises the credibility of the factual claims and lowers the probability that diligence uncovers a concealed material problem. It does not substitute for any of the missing evidence in §7.

---

**End of Document 5.**

`FACT` Grounded solely in the four source documents at repository state `68cadfb`. No market sizing, competitive analysis, customer information, or financial projection appears here, because none appears in the sources.
