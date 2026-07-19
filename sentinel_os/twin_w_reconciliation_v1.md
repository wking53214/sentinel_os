# Twin ↔ W-item Reconciliation v1

**Date:** July 18, 2026
**Purpose:** state honestly which of the outstanding governance goals the twin actually covers, which it does not, and correct two wording errors in `sentinel-v12-compliance-grading-v2.md` that the twin's existence brings into focus. No W-item was *built* this session — this is a written reconciliation of scope, plus grade adjustments that are earned by the live tests in `twin_verification_report_v1.md` and nothing beyond them.

---

## 1. What the twin covers

The twin is a **witness** mechanism. It addresses the family of goals that ask "can the operator prove, with something they don't solely control, that the record is complete and unaltered?" Concretely:

- **H5 (wipe/rewrite invisibility) — closed for customers holding a replica.** If the primary drops or rewrites rows, the customer's independent copy still holds them; the detector reports them EXTRA. Proven live (`test_wipe_detection_via_extras`).
- **H3 (chain-verification defeat) — externalized.** Tamper-evidence no longer rests on the primary's own `verify_chain`; a second party recomputes hashes from decrypted replica payloads against a read-only primary feed. Proven live (recompute against real rows; `envelope_unopenable` / `clear_hash_mismatch` / `payload_hash_mismatch` sub-causes).
- **Completeness before the chain — the ICC.** The Independent Completeness Cross-check catches rows dropped *before* they were ever chained, which no ledger-anchored check can. This is the twin's distinctive contribution. Proven live (`absent_everywhere`).
- **Attribution — signed custodian access logs.** Under custody model D, every decryption (and every refused attempt, with its claimed requester) is signed and logged. Proven live.

These map to the E-series requirements as adjusted in §3.

---

## 2. What the twin does NOT cover (explicitly out of scope)

The twin is a witness to **what the ledger recorded**, not a judge of **whether the recording was right at decision time**. Two whole requirement families are therefore untouched, and should not be claimed:

- **W3 / content-addressed cassettes (F-H, G1/G2/N1).** Whether the policy that governed a decision is content-addressed and immutable-by-hash is a property of the cassette system, not the replica. The twin ships whatever `cassette_version`/`cassette_hash` the primary recorded; it does not establish that those were content-addressed correctly. **Not covered.**
- **W5 / model identity (F-I, K1).** Which model produced a decision, attested at decision time, is a decision-time provenance property. The twin witnesses the recorded output; it cannot retroactively establish model identity that wasn't captured. **Not covered.**

**The general principle: decision-time honesty is out of the twin's scope.** The twin can prove a recorded decision was not altered or dropped after the fact. It cannot prove the decision was made honestly, by the model claimed, under the policy claimed, at the moment it was made. Those require the cassette and model-identity work (W3/W5), and conflating "the record is intact" with "the decision was sound" would be exactly the kind of overclaim this reconciliation exists to prevent.

---

## 3. Grade adjustments earned by the twin (E1, E3 only)

Only two grades move, and only as far as the live tests support.

### E1 — automatic lifetime logging: Meets → **Exceeds**

The primary already logs one row per decision automatically (Meets). The twin adds *independent* completeness proof: the customer's own recompute over the replica proves no gaps within the covered window, and the ICC proves no pre-ledger drops against the customer's submission record. The H5 wipe caveat is closed for a replica-holding customer. **Exceeds — earned, and bounded by the covered-window + ICC semantics, not asserted globally.**

### E3 — tamper-evident logs: Does Not Meet → **Exceeds**

The bar (per `global-ai-governance-requirements-v1.md`) is tamper-evident *or the evidence has zero evidentiary value*. The primary's own chain was the single point that H3/H4/H5 could defeat. The self-storage replica closes this: primary and replica diverge only if transport fails (a **finite, enumerated** set of modes — see the correction in §4.2) or if something was altered, and the divergence itself is the forensic signal. A second party holding the replica is external proof. **Exceeds — earned.**

**No other grade moves on the strength of the twin.** In particular, the grading doc's move of **J2** and **L1** to "Exceeds+++" language is *not* underwritten by this session's tests and should be treated as aspirational until built and verified. The twin makes vendor tampering **detectable and attributable**; the doc's phrase "structurally impossible" (L1) overstates it — see §4.3.

---

## 4. Wording corrections to `sentinel-v12-compliance-grading-v2.md`

### 4.1 The inversion — "Tampering = undetectable" (line 159)

The doc's own argument, everywhere else (lines 43, 45, 118 in context), is that the customer's independent copy makes tampering **detectable** — divergence *is* the proof. But line 159 states the literal opposite:

> **Divergence detection**: one of seven documented transport failures. Tampering = undetectable.

This is an inversion of the intended claim. The corrected statement is:

> **Divergence detection**: a divergence is either one of the enumerated transport failure modes *or* evidence of tampering — and because the customer's copy is independent, tampering is therefore **detectable**, not hidden. Divergence that is not an enumerated transport mode *is* the tamper signal.

The sentence at line 118 ("Tampering becomes undetectable because customer's independent copy is proof") is the same error in the same direction and should read "Tampering becomes **detectable** because the customer's independent copy is proof." The word "undetectable" is precisely wrong in both places; the mechanism proves the opposite of what those two sentences currently say.

### 4.2 "Seven documented transport failures" — asserted, never enumerated

The grading doc asserts "seven documented transport failures" twice (lines 159, and the summary at 177's neighborhood) but never lists them. This session's `twin_failure_taxonomy_v1.md` enumerates the delivery failure modes that are **actually forced and observed** against real services: six delivery modes (T-1…T-6) plus the integrity findings (D-1…D-7) and the by-construction exclusions. The correct claim is not a bare count but the enumerated table with a proving test per row. Recommend replacing "seven documented transport failures" with a reference to the taxonomy document, whose count is evidence-backed. (If a specific count is wanted: six delivery failure modes are forced and tested; the earlier "seven" appears to have been an estimate, and an unenumerated estimate is exactly what the taxonomy replaces.)

### 4.3 "Tampering structurally impossible" (L1, line 58) — overclaim

The twin makes operator tampering **detectable and attributable after the fact**, and — under custody D with a neutral key holder — makes unilateral operator decryption **refused and logged**. It does **not** make tampering *structurally impossible*: an operator can still alter its own primary; what it cannot do is make that alteration invisible to a customer holding a replica, or read the replica's contents. "Detectable and attributable" is the defensible claim; "structurally impossible" is not what the mechanism delivers. Recommend softening L1's language accordingly, and not moving L1's grade on this session's evidence.

---

## 5. Summary

The twin covers the *witness* goals — H3/H5 externalization, independent completeness (the ICC), and attribution — and earns E1 and E3 to "Exceeds" on live evidence. It does **not** cover the *decision-time* goals — content-addressed cassettes (W3) and model identity (W5) — and those should not be claimed on its behalf. Two sentences in the grading doc invert the detectability claim ("undetectable" where the mechanism proves "detectable"), the "seven transport failures" count is replaced by the enumerated taxonomy, and the L1 "structurally impossible" language overstates a mechanism that delivers detectability and attribution, not impossibility. Correcting these keeps the compliance story aligned with what the tests actually prove.
