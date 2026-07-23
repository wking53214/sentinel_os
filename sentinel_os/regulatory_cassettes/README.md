# Regulatory Cassettes

An auditor walks into a company running Sentinel and plugs in their
agency's lens — "the CFPB regs" — and Sentinel checks decisions against
that regulation's specific requirements. That is what this module is.

A **regulatory cassette is a lens, not policy.** Domain cassettes
(`cassettes/` — IVR, banking) drive judgment: they own the scoring
rules and governance parameters for their industry. A regulatory
cassette never does. It reviews decisions — recorded ones, or ones in
flight — against one regulation's expectations, and produces findings
for a human to review. It has its own registry
(`RegulatoryCassetteRegistry`), its own identity namespace
(`regulatory:<name>:<version>`), and its own directory, precisely so it
can never be mistaken, in code or in a ledger row, for the policy that
produced a decision.

## The pieces

| File | What it is |
| --- | --- |
| `../regulatory_cassette_interface.py` | The lens contract: config, mode manifest, findings, decision-material adapters, validation, registry. Kernel-adjacent — beside `cassette_interface.py`, never inside it. |
| `../regulatory_checks.py` | The reusable checkers, parameterized per regulation by a `RegulationCheckProfile`. |
| `../regulatory_deck.py` | Where lenses are inserted and run: binding, insertion events, observer review, the live judgment path with disclosure-first enforcement. |
| `cfpb_reg_b.py` | The reference lens: CFPB / ECOA / Regulation B. |

## Two modes

**Observer (default use).** Read-only review of decisions already in
the immutable ledger. `deck.observer_review()` returns a report scoped
to each inserted lens's regulation. It touches nothing in any live
path — zero production risk. The only ledger writes an observer lens
ever causes are its own insertion and removal events.

**Live (opt-in).** The lens attaches to the kernel judgment path
(`deck.judge(domain_cassette, episode)`) and reviews episodes as they
are judged. It can flag (judgment proceeds, finding attached) or block
(the deck raises `RegulatoryBlock` and refuses to return judgment until
a human reviews). Sentinel stays the judge, not the actor: a block
declines to certify; it never reaches into the deciding system.

## The three guarantees

1. **Insertion is on the record.** Inserting or removing a lens writes
   a first-class hash-chained event (`regulatory_cassette_inserted` /
   `regulatory_cassette_removed`: who, when, which lens, which mode,
   which content hash). "When was the CFPB lens active" is a direct
   query — `ledger.get_regulatory_cassette_history()` — not an
   inference.
2. **The lens is what it says it is.** Insertion content-hashes the
   lens's full configuration (including its check profile) and binds it
   through the same `bind_cassette_version` tripwire domain cassettes
   use. A changed lens presenting an unchanged version string is
   refused loud. Configuration is policy: editing a phrase list without
   bumping the lens version trips the refusal, by design.
3. **No silent live action, ever.** Every flag or block a live lens
   causes is itself written to the chain as a `regulatory_disclosure`
   event — naming the regulation and the specific check — *before* the
   action takes effect. If the disclosure write fails, the action does
   not quietly happen; the failure propagates. This is the framework's
   non-negotiable safeguard: undisclosed compliance-driven steering of
   outputs is the conduct the FTC's July 2026 Section 5 proposal treats
   as potentially deceptive, and this framework makes it structurally
   impossible rather than merely discouraged.

## What a finding is — and is not

Every check **scores and flags for human review.** A finding is not a
determination that a decision violated ECOA, Reg B, or anything else —
and an absence of findings is not a certification of compliance. Legal
compliance is a legal determination made by people.
`SCREENING_DISCLAIMER` rides in every report so this boundary cannot
quietly disappear from the output. Do not describe this module, in any
sales or compliance context, as "proof of ECOA compliance."

## Writing a new lens (CMS, NAIC, ...)

Everything regulation-specific in the reference lens is **data**: a
`RegulationCheckProfile` (generic-phrase list, placeholder patterns,
proxy-variable map, direct-protected-term map, threshold) plus identity
strings. The checkers in `regulatory_checks.py` are shared. A CMS lens
("denial notices must cite specific current criteria, not generic
algorithmic output") or a NAIC insurance-adverse-outcome lens is a new
profile over the same functions — a configuration, not a rewrite. The
test suite proves this by running a CMS-style profile through the
unmodified checker.

Authoring is Python-class-shaped this session, matching how domain
cassettes are authored. A higher-level authoring interface for
non-engineer auditors is a real, acknowledged future need — deferred
deliberately, not forgotten.

## What the checks do

- **Reason specificity** — the kernel already guarantees a reason
  *exists* on any outcome mismatch (`episode.validate_episode`; any
  mismatch, not just formal denials — approved-but-reduced counts).
  This check screens whether the reason that exists is case-specific
  ("credit score 574 is below the 620 required for the amount
  requested") or boilerplate ("does not meet our minimum credit
  standards"). Deterministic, explainable scoring; every finding shows
  the exact phrase hits and case references behind it.
- **Prohibited-basis / proxy input screen** — flags decision input
  variables that carry a protected characteristic directly, or match a
  declared proxy pattern (zip code standing in for race is the
  canonical lending example). **Declared-name screening only.** Full
  disparate-impact statistical testing is deliberately out of scope: it
  waits on an open product decision about whether Sentinel ever
  captures real protected-characteristic data or works proxy-only.
- **Input-authorization tier screen** — is each input variable on
  record as authorized to be used at all? A 7-tier ladder (T0
  prohibited .. T6 vendor-opaque) that works whether an industry has a
  real filed-variable list (NAIC, FDA PCCP, DO-178C, NERC), only a
  blacklist (ECOA, NYC LL144), or nothing at all — a profile just
  declares its own `tier_floor` and `prohibited_inputs` (both may be
  empty), and the checker never branches on industry. Every tier claim
  also carries a CONFIDENCE label — `undeclared` → `attested-
  unsupported` → `attested-accountable-unsupported` → `attested-
  accountable-evidenced` → `verified` — so a bare self-declared tier
  can never read the same as an independently verified one. The
  `verified` layer is opt-in per claim, set by whoever authored the
  profile after doing that registry integration; the checker itself
  never calls an external registry. Two live lenses in different
  jurisdictions disagreeing on the same variable's tier resolve by the
  stricter tier winning (`resolve_tier_conflict`).
- **Narrative-legitimacy screen** — when a regulation expects a
  free-text narrative on a decision (`narrative_field` declared on the
  profile), screens it for protected-characteristic-adjacent language
  and cross-references that against whether the outcome deviated from
  what was requested and whether the stated reason(s) actually mention
  the flagged content. A deviation + flagged language + a reason that
  never mentions it is a possible "laundered" reason — a real
  motivation dressed up as a policy-sounding one. A regulation that
  expects a narrative but whose decision doesn't carry one reports
  `not_screened` rather than silently never firing.
- **C2 rollup** (`rollup_c2_bias_identification`) — combines findings
  across whichever of the (up to four) C2 bias-identification
  dimensions actually ran into one PASS / FLAG / INDETERMINATE status:
  PASS only if every applicable dimension passes; FLAG if any
  applicable dimension flags; INDETERMINATE if any applicable
  dimension hasn't been evaluated — and INDETERMINATE takes precedence
  over FLAG when both occur. The fourth dimension (statistical
  outcome-equity) is not built — blocked on an open product decision —
  so a lending-shaped rollup that includes it stays honestly
  INDETERMINATE regardless of how clean the other three come back.
  Checks 1-3 can only ever prove the *negative* (nothing bad found);
  only dimension 4 could prove the *affirmative* (outcomes were
  actually fair) — never describe 1-3 passing as though it were that.

**Disclosed, unsolved, not attempted:** renaming a bad, proxy, or
undeclared-tier variable to an innocuous name defeats both the proxy
screen and the tier screen alike — same class of gap for both, and not
fixed this session (a model can encode bias through jointly-boring
declared variables with no single suspicious name). The
narrative-legitimacy screen cannot catch a sufficiently disconnected
fabricated reason, and its phrase matching is English-only to start.

## Explicitly out of scope

CPPA ADMT consumer-facing notice/opt-out/appeal rights (new capability
class); HMDA-style aggregate geographic reporting (new rollup
capability); actual hiring/insurance domain cassettes; Illinois
applicant-facing AI-use notices; the banking fraud-escalation scoring
decision (still deliberately open); C2's fourth dimension — statistical
outcome-equity — blocked on Wm's decision about whether Sentinel ever
ingests real protected-characteristic data; unicode/encoding
normalization, non-English phrase lists, and empty-vs-absent-field
handling for the narrative screen (cheap fixes, not prioritized).
