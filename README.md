# sentinel_os

Domain-blind **custody runtime** with pluggable cassettes, Episode ground-truth tracking, independent twin replication, and a Postgres-backed hash-chained ledger. Second sellable unit after the governed action gate in [`observe-perceive`](https://github.com/wking53214/observe-perceive). **Not deployable from a clean public clone today.**

## 1. Pipeline Position & Role

**CUSTODY / LEDGER.** After execution (and, on the kernel's own harness, *around* judgment). Domain-agnostic witness and judge sitting beside a decision system.

```text
Admission → Observe → Interlock → Policy → Decision → Conservation
    → Execution (GSA-815 / caller callable)
    → CUSTODY (this repo): Episode + EventV1 + OutcomeV1 + Postgres ledger + twin
```

Extracted in-process cousin: [`interconnected_delta`](https://github.com/wking53214/interconnected_delta) (no Postgres, no twin, consumes `beta.Decision`). IVR consumer: [`GSA-815`](https://github.com/wking53214/GSA-815) (submodule of this kernel).

## 2. Full System Scope & Architectural Depth

Thesis: decouple governance from the systems being governed so both can evolve while remaining verifiably connected.

### Kernel surfaces (`sentinel_os/`)

| Surface | Job |
|---|---|
| `episode.py` | Episode as ground-truth container |
| `event_v1.py` | Stamped events |
| `outcome_v1.py` | OPEN → RESOLVED/ABANDONED obligations (source of δ.obligation) |
| `canonical_fields.py` | Recomputable hash surface. **Do not encrypt this.** Crypto-shredding, if added, must wrap payload bytes *beside* the canonical digest, never mutate the field set mid-chain. |
| `cassette_loader.py` / `cassette_interface.py` / `cassette_schema.py` | Fail-closed load of domain packs |
| `cassettes/` | `ivr_cassette`, `banking_cassette`, `mortgage_cassette` |
| `regulatory_cassettes/cfpb_reg_b.py` | Regulatory overlay |
| `governance/ledger_postgres.py` | Durable ledger |
| `governance_harness.py` | `_write_decision` → `conservation/boundary.py` → Conservation Kernel; fail-close on non-acceptance |
| `conservation/` | Boundary, episode source, judgment |
| `epistemic/dit_gate.py` | Free-text governor reasoning must qualify as evidence before entering the canonical hash (DIT git pin) |
| `regulatory_checks.py` | Four-fifths and related screens (source of δ.fairness) |
| `bisg_estimator.py` | Proxy race/ethnicity estimates — **proxy, not ground truth** |
| `twin_*` / sealed demographic channel | Independent replica + crypto envelopes (`cryptography`: ECIES, Ed25519/X25519, AES-GCM, HKDF) |
| `queue_schema.py` + `lua/*.lua` | Redis claim/ack/reap (not ζ lock consensus) |
| `sage_k/` | Graph/drift/realignment experiment inside the kernel |
| `api_server_v2.py` | HTTP API |

Cassette contract: domain packs declare capabilities; unknown/unevaluated cassette state fail-closes the loader. Judgment (`Cassette.judge`) is **after-the-fact** quality, distinct from live α-ζ-β decisioning.

### Conservation seam

Every governed decision is submitted to [`Conservation_Kernel`](https://github.com/wking53214/Conservation_Kernel) `@25145aa`. Ledger write does not proceed on REJECT/UNVERIFIABLE.

## 3. What It Does NOT Do / Non-Goals

- Not an AI model, agent framework, prompt manager, or guardrail library.
- Not a replacement for legal interpretation or a single regulation implementation.
- Not the observe-perceive orchestrator (never imported by it).
- Does not issue human grants (attestation helpers exist; grant lifecycle is missing).
- Does not implement Raft for ζ locks.
- Does not crypto-shred GDPR-style while preserving canonical_fields (twin encryption ≠ shredding).

## 4. Brutally Honest Current Status & Gaps

Measured 2026-09-09 from clone `5341e3d`, 968 collected, **without Postgres**: 658 pass, 228 skip, 39 fail, 38 error.

| Gap | Detail |
|---|---|
| Root package metadata | Kernel `requirements.txt` exists under `sentinel_os/sentinel_os/`. Repo root does not install as a product. |
| Required services | Postgres, Redis. Twin and live paths need `cryptography`, keys. |
| Private `CNS` | `cns @ git+…/cns.git@9f8f3fed…` — **private**. Public clone cannot satisfy sage_k graph types. |
| DIT pin | `dit @ git+…/DIT.git@26cee7b9…` (feat/sync-evaluate head). Squash-merge rewrites SHA. |
| Anthropic | Governor path. `httpx<0.28` pin required for anthropic 0.116.0. |
| No Cassette SDK | Cassettes are in-tree modules, not a versioned SDK. |
| OTLP | Optional if `OTEL_EXPORTER_OTLP_ENDPOINT` set; not a product telemetry plane. |
| 93 TODOs | Across kernel/tests. |
| Commercial | Product *candidate* in regulated industries; **least evidenced**. Freeze lifted 2026-09-11; findings not superseded. |
| Nested copies | OBSERVE vendors a stale tree of this kernel. Do not edit the wrong copy. |

## 5. Core Invariants & Guarantees

- Fail-closed cassette load and conservation boundary.
- Recomputation over trust: `canonical_fields` hashed; stored strings not believed.
- Actor self-report is not ground truth for judgment (BISG is estimated; sealed demographics are not mixed into judgment plaintext).
- Policy/approval from other layers ≠ this kernel minting authorization.
- Twin is an independent replica, not a shared-memory echo (when infra is actually up).
- Outcome obligations do not silently invent a kind for decisions that declare none.

## 6. Inputs, Outputs & Type Contracts

`Episode` / `EventV1` / `OutcomeV1` / cassette `judge()` / ledger rows with `current_hash` chained from previous. Canonical field lists in `canonical_fields.py` are the hash surface. Do not add encrypted blobs *into* those fields.

## 7. Stack Integration Topology

```text
observe-perceive  ✗ does not import sentinel_os
GSA-815           → vendor/sentinel_os submodule
Conservation_Kernel @25145aa  ← mandatory boundary
DIT               ← epistemic gate on governor text
private CNS       ← graph types
δ (interconnected_delta)  ← in-process extract of outcome_v1 + fairness
ICEBURG/ICEBERG   ← lineage; not imported (ICEBERG_* env var names are fossils)
```

Cassettes: IVR, banking, mortgage + CFPB Reg B overlay. New domains belong here as modules, not as forks of the kernel.

Proprietary. Copyright (c) 2026 William N. King. All rights reserved. See LICENSE. See `docs/pass3/` for the v3 architecture briefs (investor/auditor/ops); treat them as briefs, not as "deployable today".
