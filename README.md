# sentinel_os

Domain-blind **custody runtime** with pluggable cassettes, Episode observation and provenance tracking, independent twin replication, and a Postgres-backed hash-chained ledger. Second product candidate after the governed action gate in [`observe-perceive`](https://github.com/wking53214/observe-perceive). **Needs Postgres, Redis and operator-supplied configuration to run; see `sentinel_os/DEPLOYMENT.md`.**

New to the vocabulary (cassette, twin, ζ, δ, TACK and so on)? See [`docs/GLOSSARY_v1.md`](docs/GLOSSARY_v1.md).

## 1. Where this fits in STACK

STACK is a family of separate repositories that each do one governance job. This one does **custody**: it observes what a decision system did, judges the outcome afterward against rules fixed beforehand, and keeps a tamper-evident record that a party outside the system can check. It does not make or carry out the business decision. In STACK's layer vocabulary it supplies Layer 5, verdict receipts (see [`STACK.md`](STACK.md)).

The live path, in the order the sibling repos describe it:

```text
Admission → Observe/Keys → Locks → PERCEIVE → Decision → Conservation
    → Execution (a caller-supplied callable; GSA-815 for IVR)
    → CUSTODY (this repo): Episode + EventV1 + OutcomeV1 + Postgres ledger + twin
```

That is an order, not an import chain. The live decision path is [`observe-perceive`](https://github.com/wking53214/observe-perceive), which never imports this repo. This repo is also not one of the five repositories of the evidence spine (HERALD → TIE → Conservation_Kernel → Governance_Gateway → CCC), and the CNS spine run does not cover it. It meets that spine at one point: it calls Conservation_Kernel on every governed decision.

| Repo | Its role in STACK | Relationship to this repo |
|---|---|---|
| [`observe-perceive`](https://github.com/wking53214/observe-perceive) | Hub of the governed action stack; the live decision path and the first product | Never imports this repo. Custody comes after it in the conceptual order only. |
| [`interconnected_alpha`](https://github.com/wking53214/interconnected_alpha) → [`zeta`](https://github.com/wking53214/interconnected_zeta) → [`beta`](https://github.com/wking53214/interconnected_beta) → [`delta`](https://github.com/wking53214/interconnected_delta) | Extracted decision spine: Keys, Locks, Decision, Custody | δ is the in-process cousin of this repo. It reimplements outcome obligations and the four-fifths screen without Postgres or a twin, and consumes β's `Decision`. It does not import this repo. |
| [`Conservation_Kernel`](https://github.com/wking53214/Conservation_Kernel) `@7b08c79` | Repo 3 of 5 in the evidence spine; checks that a transformation did not silently change protected distinctions | Hard dependency. Every governed decision goes through it, and nothing is written unless it returns ACCEPTED. |
| [`CNS`](https://github.com/wking53214/CNS) `@v2.3.0` | Shared contracts that the repos plug into | Dependency: `cns.gate` (the types behind verdict receipts) and `cns.graph` (graph types used by `sage_k`). |
| [`DIT`](https://github.com/wking53214/DIT) `@V0.1.0` | Output-integrity gate for generated text | Dependency: `epistemic/dit_gate.py` evaluates the governor's free-text reasoning. |
| [`GSA-815`](https://github.com/wking53214/GSA-815) | IVR execution and learning application | Consumes this kernel and vendors it as the submodule `vendor/sentinel_os`. The IVR code was extracted out so this kernel stays domain-blind. |

## 2. Full System Scope & Architectural Depth

Thesis: decouple governance from the systems being governed so both can evolve while remaining verifiably connected.

### Kernel surfaces (`sentinel_os/`)

| Surface | Job |
|---|---|
| `episode.py` | Episode as a structured observation and provenance record (not ground truth) |
| `event_v1.py` | Stamped events |
| `outcome_v1.py` | OPEN → RESOLVED/ABANDONED obligations (source of δ.obligation) |
| `canonical_fields.py` | Recomputable hash surface. **Do not encrypt this.** Crypto-shredding, if added, must wrap payload bytes *beside* the canonical digest, never mutate the field set mid-chain. |
| `cassette_loader.py` / `cassette_interface.py` / `cassette_schema.py` | Fail-closed load of domain packs |
| `cassettes/` | `ivr_cassette`, `banking_cassette`, `mortgage_cassette`. The kernel's `GovernanceHarness` deliberately refuses a cassette that declares telephony, so IVR does not run through it |
| `regulatory_cassettes/cfpb_reg_b.py`, `regulatory_deck.py` | Regulatory overlay (CFPB Reg B) and the deck lenses are inserted into |
| `contract_cassettes/`, `contract_attestation.py` | Contract compliance attestation: each counterparty can check that their own data-use contract is honored |
| `obligation_sweep.py`, `human_review_ledger.py` | Cohort assembly for the fairness screens (a deployer must schedule it); recording of a human's concur, override or escalate |
| `governance/authorized_by_attestation.py`, `tools/verify_receipts.py` | HMAC attestation on each ledger row, and an offline verifier that prints one of `VERIFIED`, `TAMPERED`, `TRANSPLANTED`, `SEED_FORGED`, `TRUNCATED`, `UNATTESTED` |
| `governance/ledger_postgres.py` | Durable ledger |
| `governance_harness.py` | `_write_decision` → `conservation/boundary.py` → Conservation Kernel; fail-close on non-acceptance |
| `conservation/` | Boundary, episode source, judgment, and the transport gateway the boundary submits through |
| `epistemic/dit_gate.py` | Evaluates free-text governor reasoning for evidentiary quality (DIT git pin). Default mode `record` logs findings without changing the decision. `terminal` and `strict` modes can refuse qualifying reasoning. An unavailable evaluation does not block. Mode is set by `SENTINEL_EPISTEMIC_MODE`. |
| `regulatory_checks.py` | Four-fifths and related screens (source of δ.fairness) |
| `bisg_estimator.py` | Proxy race/ethnicity estimates: **proxy, not ground truth** |
| `twin_*` / sealed demographic channel | Independent replica + crypto envelopes (`cryptography`: ECIES, Ed25519/X25519, AES-GCM, HKDF) |
| `queue_schema.py` + `lua/*.lua` | Redis claim/ack/reap (not ζ lock consensus) |
| `sage_k/` | Experiment, not on the custody path: a seeded simulation kernel (Fortress), a hash-chain adapter and a call-graph extractor. The drift and harness code was retired |
| `api_server_v2.py` | HTTP ingress: API-key auth, rate limiting, backpressure, and the human-selection endpoint |

Cassette contract: domain packs declare capabilities; unknown/unevaluated cassette state fail-closes the loader. Judgment (`Cassette.judge`) is **after-the-fact** quality, distinct from live α-ζ-β decisioning.

### Conservation seam

Every governed decision is submitted to [`Conservation_Kernel`](https://github.com/wking53214/Conservation_Kernel) `@7b08c79`. Ledger write does not proceed on any status other than ACCEPTED (REJECTED, REQUIRES_AUTHORIZATION, or REQUIRES_VERIFICATION).

## 3. What It Does NOT Do / Non-Goals

- Not an AI model, agent framework, prompt manager, or guardrail library.
- Not a replacement for legal interpretation or a single regulation implementation.
- Not the observe-perceive orchestrator (never imported by it).
- Does not verify who a human is or issue authority to anyone. `authorized_by` is attested with a service-level key: it shows the string was written by a key holder and has not been altered, not that the named person authorized anything.
- Does not implement Raft for ζ locks.
- Does not crypto-shred GDPR-style while preserving canonical_fields (twin encryption ≠ shredding).

## 4. Brutally Honest Current Status & Gaps

**Test status (measured 2026-11-10 on `main` @ `8a8cd30`, run as root with PostgreSQL 16 and Redis running locally, using the CI command `python -m pytest . ../tools` from `sentinel_os/`):** 1181 passed, 0 failed, 22 skipped. An earlier run the same day (on `cd5b630`, the same code) had 12 failures, all in `test_twin_live.py`, where a twin service never reported healthy. Two later full runs, and the file run alone, passed, so that failure did not reproduce and its cause is unknown (a leftover process or port from an earlier run is the suspect, unconfirmed). Treat `test_twin_live.py` as environment-sensitive: it starts services under three OS accounts and needs `sudo scripts/twin_ensure_services.sh`. Without Postgres and Redis, many more tests fail or skip. CI (`.github/workflows/tests.yml`) is the executable spec; these numbers are a point-in-time snapshot and will go stale.

| Gap | Detail |
|---|---|
| Root package metadata | Kernel `requirements.txt` exists under `sentinel_os/` (the package directory). Repo root does not install as a product. |
| Required services | Postgres, Redis. Twin and live paths need `cryptography`, keys. |
| `CNS` pin | `cns @ git+…/CNS.git@v2.3.0` (release tag). Public as of 2026-10-10: a clean install resolves it with no token. The ledger imports `cns.gate` and sage_k imports `cns.graph`. |
| DIT pin | `dit @ git+…/DIT.git@V0.1.0` (the tag points at commit `a690dc3`). Pinned to a tag so a squash merge cannot make it unreachable. DIT's own README names a different commit for this repo; trust `sentinel_os/requirements.txt`. |
| Anthropic | Governor path. `httpx<0.28` pin required for anthropic 0.116.0. |
| Conservation root attestation | Event-based root attestation applies only when `observed_events` are supplied. Without them, the boundary accepts an explicitly unguarded source root (`allow_unguarded_roots=True` default). Supplied events are caller claims, not independent proof of external reality. |
| No Cassette SDK | Cassettes are in-tree modules, not a versioned SDK. |
| OTLP | Optional if `OTEL_EXPORTER_OTLP_ENDPOINT` set; not a product telemetry plane. |
| Open TODO markers | None in the code (checked 2026-11-10). |
| Commercial | Product *candidate* in regulated industries; **least evidenced**. This repo documents no production customer. |
| Vendored copy | GSA-815 vendors this kernel as a submodule at `vendor/sentinel_os`. Edit this repo, not the copy. |
| Service-level keys | Attestation keys belong to the service, not to a person. A leaked key forges every signature made under it, and nothing identifies which key holder wrote a row. |
| Human review | A human's concur, override or escalate can be recorded through the API. There is no review screen, and the automated decision path never calls it. |
| Twin trust | The twin protects only rows that reached the replica before any tampering. Registering a replica needs an operator secret, an operator-pinned customer key and a customer signature; see `SECURITY.md`. |

## 5. Core Invariants & Guarantees

- Fail-closed cassette load and conservation boundary.
- Recomputation over trust: `canonical_fields` hashed; stored strings not believed.
- Actor self-report is not ground truth for judgment (BISG is estimated; sealed demographics are not mixed into judgment plaintext).
- Policy/approval from other layers ≠ this kernel minting authorization.
- Twin is an independent replica, not a shared-memory echo (when infra is actually up).
- A decision that declares no obligation owes none, and an unreadable declaration raises instead of being skipped.

## 6. Inputs, Outputs & Type Contracts

`Episode` / `EventV1` / `OutcomeV1` / cassette `judge()` / ledger rows with `current_hash` chained from previous. Canonical field lists in `canonical_fields.py` are the hash surface. Do not add encrypted blobs *into* those fields.

## 7. Dependencies and Pins

The relationships are in section 1. In short:

```text
Conservation_Kernel @7b08c79   mandatory boundary (installed by commit)
CNS @v2.3.0                    gate contract (ledger) and graph types (sage_k)
DIT @V0.1.0                    epistemic gate on governor text
GSA-815                        vendors this repo at vendor/sentinel_os
observe-perceive, δ            do not import this repo
ICEBURG/ICEBERG                lineage names; not imported (ICEBERG_* env var names are fossils)
```

Cassettes: IVR, banking, mortgage, plus the CFPB Reg B overlay and a contract-attestation lens. New domains belong here as modules, not as forks of the kernel.

Apache-2.0. The documents are indexed in [`docs/README.md`](docs/README.md), which separates current documents from the dated `docs/pass3/` briefs. Those briefs describe the code as it was in July to September 2026 and are not a description of what runs today.
