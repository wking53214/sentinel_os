# Glossary (v1)

Plain-English meanings for the symbols, codenames and jargon used across this repo.

**How to trust this page.** Written 2026-10-10 against `main` @ `4f2be13`. Each entry is drawn from this repo's own README, STACK.md, COMPLIANCE.md, module docstrings and tests. Where a term is defined in another repo and this repo does not explain it, the entry says so instead of guessing. If a definition here disagrees with the code, the code wins; please fix this page.

## The core objects

- **Custody runtime.** What this repo is: a domain-blind recorder and judge that sits beside a decision system. It does not make the business decision. It keeps an independent, tamper-evident record of what was asked, what happened, and what was decided.
- **Kernel.** The domain-agnostic core in `sentinel_os/`. Anything specific to one industry lives in a cassette instead.
- **Cassette.** A domain pack: the parameters, capabilities and judging logic for one use case. Three ship today: IVR (phone menus), banking and mortgage. A cassette that is incomplete or unrecognized is refused at load (fail-closed).
- **Cassette binding.** A ledger entry that records a cassette version together with the hashes of its parameters and its decision code. Re-using the same version name with different content is refused.
- **Episode.** The kernel's unit of judgment: what was requested, what actually happened according to independent observation, what the acting system claims happened, and the timeline between. If anything differs from the request, the episode must carry a reason or it does not validate.
- **EventV1.** A stamped event inside an episode.
- **OutcomeV1 / obligation.** An obligation is something owed that stays OPEN until it is RESOLVED or ABANDONED. OutcomeV1 is its durable record. It is the source of the "δ.obligation" signal.
- **Provenance stamp.** Every claim is marked `verified`, `attested` or `estimated`, and the three are never treated as interchangeable. If something is unknown, the system records why and what would settle it.
- **Governor.** The AI model consulted to approve or reject a decision. An answer the system cannot parse or validate never counts as an approval (fail-closed).
- **Fail-closed.** When something errors or is uncertain, the outcome is "not approved" or "refused", never "allowed".
- **Judgment (`Cassette.judge`).** After-the-fact quality assessment of an episode. It is separate from the live decision step (see "α, ζ, β, δ" below).

## The ledger and tamper evidence

- **Ledger.** The durable Postgres log of governance decisions. Append-only: database triggers block ordinary updates and deletes.
- **Hash chain.** Each entry stores a hash of its own content plus the previous entry's hash (`current_hash`). Editing an old row breaks every hash after it. This catches accidental damage and naive edits; COMPLIANCE.md explains why it is not proof against a determined database owner.
- **Canonical fields.** The fixed list of fields that feed the hash. They can be recomputed from stored data, so the system recomputes instead of trusting stored strings. Do not encrypt these fields.
- **Supersession.** A later entry that corrects or overrules a decision without changing the original. It points to the original by ID and by hash.
- **Human selection (concur / override / escalate).** A recorded human response to a governor's verdict. The recording mechanism exists and is tested; nothing in the live path calls it yet (see COMPLIANCE.md, Known Limitations).
- **Verdict receipt.** A governance decision bound to a digest of exactly what it judged, signed over its content, hash-chained, and anchored outside the database. An outside auditor can check it offline.
- **`subject_digest`.** The CNS fingerprint of what a decision judged. It ties a verdict to its input.
- **Head anchor.** A copy of the chain's latest position kept outside the database after every append, so an offline check can notice a chain that was cut short.
- **Attestation policy marker.** A chain entry recording when signing became mandatory. From that point on, an unsigned accountable claim is reported as `UNATTESTED`.
- **Offline verifier (`tools/verify_receipts.py`).** Reads an exported ledger and prints exactly one result:
  - `VERIFIED`: everything checks out.
  - `TAMPERED`: a row's content no longer matches its hash.
  - `TRANSPLANTED`: a row carries another row's subject digest.
  - `SEED_FORGED`: a seed the server would not have derived.
  - `TRUNCATED`: rows were removed from the end.
  - `UNATTESTED`: no valid signature from a trusted key.

## The witness twin

- **Twin.** A read-only replica that ships each ledger row, encrypted, to a store the customer controls, using a key Sentinel does not hold. It is the independent second record. It is off unless `TWIN_TARGETS_FILE` is configured.
- **DAP v1 (Divergence Attestation Protocol).** The protocol the twin uses to compare the live ledger against the customer's replica.
- **Twin verdicts.** `MATCH`, `DIVERGE` (the two disagree), `MISSING` (on the primary, absent on the replica), `EXTRA` (on the replica, absent on the primary), `PENDING` (not yet due).
- **ICC (Independent Completeness Cross-check).** A count of what must exist, taken from the customer's own submission record, used to catch records dropped before the ledger ever saw them.
- **Covered window.** The span of rows already synced to a replica. The twin only protects rows that reached it before any tampering.
- **Sealed demographic channel.** Demographic data kept encrypted beside the judgment record, never mixed into the plaintext the judgment sees.

## Gates, guards and plumbing

- **Conservation boundary / Conservation Kernel.** A separate, pinned repo that independently verifies each governed decision. If it returns REJECT or UNVERIFIABLE, the ledger write does not happen: no durable state without conservation verification.
- **DIT gate (`epistemic/dit_gate.py`).** Checks whether the governor's free-text reasoning actually qualifies as evidence before it is sealed into the hash. Without it, a hedge like "this might be safe, I believe" would become permanent evidence of approval. DIT is a separate pinned repo.
- **Loop guard.** Detects a governance decision repeating itself. A repeat is treated as a signal, not as proof.
- **Backpressure.** A number that says how long a producer should wait as the queue fills. It computes the delay only; the caller decides what to do. Ingress uses it to slow submissions.
- **Transmission queue and worker.** A Redis-backed queue where a worker claims a job, keeps a heartbeat while working, then acks it. A job whose ledger write failed is failed as retryable and never acked.
- **Ingress.** The HTTP front door, `api_server_v2.py`.
- **`wiring_verify`.** A static analysis tool in `tools/` that answers "can this entry point actually reach this feature?" from the code's call graph. It reports `REACHABLE`, or `UNVERIFIABLE_STATICALLY` when dynamic code means it cannot know.

## Fair-lending checks

- **Regulatory lens.** An auditor-style overlay that screens decisions against one regulation's rules. `regulatory_cassettes/cfpb_reg_b.py` is the CFPB Reg B (fair lending) reference lens.
- **Regulatory deck.** The holder that lenses are inserted into.
- **Observer vs LIVE mode.** Observer mode (the expected default) is read-only against the existing ledger and produces a review report; it touches no live decision. LIVE mode is opt-in: the lens attaches to the judgment path and reviews episodes as they are judged. It can flag or block, and every such action is first logged to the ledger as a disclosure event, so there is no silent path.
- **Flag-only.** A LIVE lens raises a finding but does not block the decision. The mortgage worker runs the Reg B lens this way. Cohort-level signals are never allowed to auto-block, by design.
- **Screening signal.** Every finding is a prompt for human review, not a legal conclusion.
- **Cohort checks and the sweep.** Three of the six dimensions look across groups of decisions, not single ones. `obligation_sweep.py` assembles that data. Nothing in this repo schedules it; running it is a deployer's job.
- **Four-fifths screen.** A standard rule of thumb that flags a group whose favorable-outcome rate falls below four fifths of the best group's rate.
- **BISG.** A method that estimates race or ethnicity from name and address. The result is an estimate, not ground truth, and it is off unless `SENTINEL_BISG_RESEARCH_MODE=true`.

## Other repos and names

- **α, ζ, β, δ (alpha, zeta, beta, delta).** Stages of a decision spine that live in the sibling repos `interconnected_alpha`, `interconnected_zeta`, `interconnected_beta` and `interconnected_delta`. What this repo says about them:
  - **β** produces a `Decision`.
  - **δ** is an in-process extract of this kernel's ideas (no Postgres, no twin) that consumes `beta.Decision`. Its "obligation" signal comes from OutcomeV1 and its "fairness" signal from `regulatory_checks.py`.
  - **ζ** is the locking stage ("ζ locks"). This repo does not implement Raft or any lock consensus; its Redis queue is not that.
  - **α** is the first stage of the spine. This repo does not describe what it does.
- **α-ζ-β decisioning.** The live decision path in those sibling repos. This kernel does not make that decision; it records and judges it afterward.
- **CNS.** A pinned shared package. This repo uses `cns.gate` (the types behind verdict receipts and `subject_digest`) and `cns.graph` (graph types used by `sage_k`). Pinned to v1.4.0.
- **TACK (≡TACK) and "Layer 5".** The layered architecture this repo is one part of. This repo implements Layer 5, verdict receipts. The other layers live elsewhere.
- **`sage_k`.** An experiment inside the kernel: a seeded simulation kernel (Fortress), a hash-chain adapter, and a call-graph extractor. Not part of the core custody path.
- **GSA-815.** The IVR (phone menu) consumer repo. It includes this kernel as a submodule.
- **observe-perceive.** The governed action gate, the first sellable unit. This repo is the second. Neither imports the other.
- **HERALD.** A separate natural-language layer. This repo only holds the results of an adversarial test campaign run against it (`docs/audit/HERALD_CAMPAIGN_2026-08-26.md`).
- **STRIDE salvage.** The loop guard and backpressure modules were rescued from a repo called STRIDE before it was deleted. Their docstrings record that origin.
- **ICEBERG and ICEBURG.** Older lineage names, not imported. Many environment variables still start with `ICEBERG_` for that historical reason. They are names, not meaning.
