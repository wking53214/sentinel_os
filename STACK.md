# Stack role — sentinel_os

**CUSTODY / domain runtime** — domain-blind governance kernel: episodes, events, cassettes, hash-chained ledger, twin witness, outcome obligations, provenance stamps (`verified` / `attested` / `estimated`).

Second sellable unit after the governed action gate in [observe-perceive](https://github.com/wking53214/observe-perceive). Decision spine that feeds obligations: [interconnected_alpha](https://github.com/wking53214/interconnected_alpha) → [zeta](https://github.com/wking53214/interconnected_zeta) → [beta](https://github.com/wking53214/interconnected_beta) → [delta](https://github.com/wking53214/interconnected_delta).

```text
Live path: Admission → OBSERVE/Keys → Locks → PERCEIVE → Decision → Conservation → Execution → Custody (this repo)
```

**Verdict receipts (TACK Layer 5):** every governance decision the ledger writes is bound to the CNS digest of what it judged, signed over its content with a key the agent does not hold, hash-chained, and anchored outside the database. `tools/verify_receipts.py` verifies an export offline and prints one of `VERIFIED`, `TAMPERED`, `TRANSPLANTED`, `SEED_FORGED`, `TRUNCATED`, `UNATTESTED`. Attestation is enforced by default; the ledger refuses to start without `ICEBERG_LEDGER_ATTESTATION_KEY`.

**Not clone-and-run without services** (Postgres, etc.). See README for commercial status and dependencies.

See [README.md](README.md) for full architecture.
