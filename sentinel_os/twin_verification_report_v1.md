# The Twin (customer-DR witness replica) — Verification Report v1

**Components:** `twin_custody.py`, `twin_receiver.py`, `twin_shipper.py`, `twin_sync_worker.py`, `twin_detector.py`, `twin_custodian.py`, `twin_migrate.py`, `twin_probe.py` — the DAP v1 reference implementation (2,784 lines incl. tests).
**Date:** July 18, 2026
**Environment:** Redis 7.x, Postgres 16.14, Python 3.12.3, `cryptography` 46.0.6, psycopg2 2.9.x, fastapi + uvicorn, httpx. Repo @ `5e61d63` (clean tree == `origin/main`) **+ this session's new files only**. Zero modifications to any existing tracked file — confirmed by `git status` showing all ten twin files as untracked (`??`) and no tracked file modified.
**Method:** 27 tests total (9 custody-layer + 18 live integration). Every live test runs against **real Redis, real Postgres under three distinct credential identities (`sentinelsvc`, `twincustomer`, `twincustodian`), and real HTTP receiver/custodian processes started as those OS users**. No mock of Redis, Postgres, the queue, the crypto, or the HTTP transport anywhere. **27/27 passed in a single clean run** (`101.26s`), after the fixes described in §4.

---

## 1. What the twin is

A customer-controlled disaster-recovery replica that acts as an external witness to the V12 governance ledger. Sentinel ships every committed ledger row, sealed to a key it does not hold, to a replica the customer (or a neutral custodian) controls. A customer or regulator can then prove three things the ledger alone cannot prove: that the operator cannot read the replica, that any divergence between replica and primary is detectable and attributable, and — via an operator-independent cross-check — that nothing was dropped before it was ever chained. The protocol is specified in `twin_attestation_spec_v1.md`; this report is the evidence that the implementation does what the spec says.

---

## 2. Proven live

Each item below is a real operation against real services, not a behavior counted from logs.

**Crypto and custody (custody suite, 9/9):**
- Envelope round-trip; wrong-key refusal; **the public key Sentinel holds cannot stand in for the private key** (the exact "operator tries with what it has" shape); AAD slot-binding refuses three wrong-slot variants; GCM tag refuses a single flipped ciphertext byte; Ed25519 sign/verify accepts and rejects correctly; structural validation catches missing fields, bad base64, and sub-tag-length ciphertext.
- **Hash recomputation validated against real ledger rows**: all five live governance-decision rows recompute to their stored `current_hash`; a one-field edit is caught. This is the anchor that lets a decrypted replica payload be proven to be the preimage of the primary's hash.

**End-to-end pipeline and confidentiality (live suite):**
- Full path — seed → ship as `sentinelsvc` → sync → customer opens and deep-verifies — across all three credential boundaries.
- **Sentinel-cannot-decrypt, enumerated and proven three ways**: the public key fails as a private key; the `sentinelsvc` identity is refused reading the customer's key file (real OS permission denial); the `sentinelsvc` identity is refused connecting to the customer's replica database (real Postgres `CONNECT` denial).

**Divergence detection:**
- Clean match verdict with deep verification on every row.
- **Tamper distinguished from omission**: a byte-flipped ciphertext → `envelope_unopenable`; a clear-hash edit → `clear_hash_mismatch`; both distinct from each other, and one untouched row still MATCHes (asserted `match == 1`).
- **Forced omission caught by two independent layers**: a row withheld at ship time is caught by the feed comparison; a row the customer submitted that never reached the ledger is caught by the ICC as `absent_everywhere`.
- **Wipe detection**: rows present on the replica but gone from the primary are all reported EXTRA.
- **Multi-site independence**: two sites, one ledger; a tamper on site-b leaves site-a's verdict CLEAN and flags site-b's single divergence.
- **SLA and clock-skew immunity**: an expected-but-unshipped row is PENDING within SLA and MISSING after; rewriting `received_at` five years into the future changes no verdict.

**Transport (real queue, real HTTP):**
- Offline replica → jobs back up as `SERVICE_INTERRUPTION`, nothing dead-letters, all recover when the receiver returns.
- Partition mid-flight (accept-then-stall socket) → `NETWORK_LATENCY` in the error trail.
- Duplicate delivery idempotent (identical → `duplicate`; altered → `409`).
- Out-of-order delivery (3,2,1) → chain reconstructs.
- Torn delivery → `DATA_CORRUPTION` dead-letter → `requeue_from_dlq` + re-ship → delivered.
- **Never blocks primary**: with the twin queue backed up against a dead receiver, five primary ledger appends complete in well under the asserted bound.

**Custody model D and migration:**
- Custodian grant/refusal attribution: a valid customer-signed decrypt is granted and logged as `customer-audit`; a forged request claiming to be `sentinel` is refused `403` and still logged with the claimed requester; the audit log verifies (chain + custodian signatures).
- A→D migration re-seals the backlog with slot-binding preserved: the old key fails afterward, the custodian opens and deep-verifies every entry, and a signed `custody_migration` event is on the log.

**Operational recovery:**
- Restore drill: `pg_dump` → drop → create → `pg_restore` the customer replica DB, run as `twincustomer`; entry count and custody log survive and every restored entry still deep-verifies. This is the doc's own E4 retention bar exercised as a live drill rather than asserted.

**Regulator probe:**
- Clean replica → `conformant: true`, exit 0. Seeded one tamper + one omission → `conformant: false`, exact finding counts, exit 2. Run as the customer identity.

---

## 3. NOT proven (stated plainly)

- **Retention pruning at scale.** The restore drill proves a replica DB round-trips intact; it does **not** prove a 7-year retention/pruning policy over a large corpus. `retention_days` is stored per replica and honored by nothing yet — pruning is a design note (see §5), not implemented behavior.
- **Single-tenant hash feed.** The primary feed uses one `ledger_reader` credential over the whole ledger. In a real multi-customer deployment each customer must see only their own rows; that row-level scoping is **not** built — the covered-window mechanism scopes *detection*, not *access*. A production feed needs per-customer filtering at the credential or view layer.
- **Concurrency at production scale.** Tests exercise correctness with modest row counts. Sustained high-throughput shipping, many replicas fanned out concurrently, and the sync worker under heavy parallel load were not load-tested.
- **Custodian as a hardened service.** The custodian's registration and audit-log reads are unauthenticated in the reference implementation; state is file-backed JSON. Production needs customer authN in front and durable storage. The wire contract and log format are the normative parts; the hardening is not done.
- **Receiver authN.** Same caveat — registration, custody-event, and read routes rely on the DB credential boundary and `127.0.0.1` binding, not on customer authentication. Production fronts them.
- **TWIN_SYNC_CORRUPT_ONCE hook honesty.** The torn-delivery test injects corruption via an env-var hook consumed inside the worker (`os.environ.pop`). This is a genuine truncation of real ciphertext producing a real `422`, but it is triggered by a test hook rather than a naturally corrupted wire — the corruption is real, its *trigger* is synthetic. Flagged, not hidden.

---

## 4. Regressions and design corrections I made during the build (stated plainly)

**The witness-window scoping question — a real design decision, not a test hack.** My first detector compared the *entire* primary ledger against a replica that holds only its own slice, so every unrelated ledger row showed as MISSING. That surfaced as two failing tests. The fix was to scope detection to a **covered window** (`primary_id_range`, defaulting to the replica's own `min..max` span): within-window gaps are MISSING, while omissions at the very edges of the stream fall to the Independent Completeness Cross-check against the customer's submission record. This is defensible and now specified (§6.1, §6.4 of the spec) — a witness is accountable for the window it covers, and the ICC is the operator-independent layer that catches edge and pre-ledger drops the feed comparison structurally cannot. The SLA test then had to express an explicit covered window spanning both its expected rows, because "an expected row not yet arrived" is exactly what `primary_id_range` is for. I want this called out rather than buried: the scoping is a real narrowing of what "missing" means, paired with a second layer that closes the gap it opens.

**`call_sid` lives in two places.** Base `append()` ledger rows leave the `call_sid` column null and carry the identifier inside `data`; only `append_decision` populates the column. My seed helper and feed queries initially read the empty column, so the forced-omission skip hook got an empty sid and delivered the row it was meant to drop. Fixed with a `COALESCE(call_sid, data->>'call_sid')` fallback in the shipper and tests — faithful to how both row kinds actually behave, not a workaround.

**Credential impersonation in tests.** My in-test tamper simulations connected to customer databases over TCP without a password, but `twincustomer` is peer-auth and the pytest process runs as root, which cannot impersonate the customer. Fixed with a `customer_sql()` helper routing at-rest mutations through `runuser -u twincustomer` — which makes the credential boundary *real* in the tests rather than assumed.

---

## 5. Retention design note (the E4 bar, by design + drill)

DAP stores `retention_days` per replica (default 2557 ≈ 7 years, matching the blended requirement). Pruning is intentionally **not** automatic in v1: destroying witness evidence is a step that should be deliberate and logged, not a background sweep. The intended mechanism is a customer-run, custody-logged prune that (a) refuses to prune rows inside the retention window, (b) records a signed `retention_prune` custody event with the id range removed, and (c) leaves the hash chain of *remaining* rows verifiable. The restore drill in this report proves the other half of E4 — that a replica can be dumped and restored intact — which is the part that is actually implementable and testable today. The prune itself is specified as future work, not claimed as done.

---

## 6. Environment defect observed (pre-existing, unrelated to the twin)

Independent of this build, the existing whole-repo test suite has a test-isolation defect worth recording because it affects anyone running `pytest` across the tree:

- **Namespace env leak.** `test_api_server_v2.py` sets `TRANSMISSION_NAMESPACE` process-wide; because `test_queue_identity_converter.py` spawns subprocesses via `os.environ.copy()`, the leaked namespace produces 2 false failures when the suites run in one process. The converter itself is sound (its own file passes 4/4 in isolation).
- **Collection-order coupling.** A legacy `Tests/` directory (capital T) collects first and its harness import trips `test_T10`.
- **TLS cert fixtures.** 4 TLS tests require gitignored certs and fail on a fresh clone.

From a fresh clone the whole-repo run is 235/242 for these reasons — **none** of which are twin-related. The twin suites (`test_twin_custody.py`, `test_twin_live.py`) are self-contained and pass 27/27 together. **This is exactly the kind of cross-suite interference that the agreed next step — a full red-team of the entire test suite, existing + twin — is meant to surface and fix.** It is flagged here, left unfixed by prior decision, and owned by that next session.

---

## 7. Boundary caveat: what "credential isolation" means in this environment

The three identities (`sentinelsvc`, `twincustomer`, `twincustodian`) are real OS users with real Postgres roles and real peer-auth boundaries, and the denials in §2 are genuine. **However**, this all runs inside a single dev container where the build process has root. Root can, of course, read any file and become any user — so the isolation proven here is *role-level and credential-level*, demonstrating that the **application identities** cannot cross the boundaries, not that a compromised host is contained. In production the customer's replica and key material live on infrastructure the operator does not have root on; that physical/organizational separation is the real boundary, and this report proves the software honors it, not that a root adversary is defeated. Stated so the claim is not overread.

---

## 8. Summary

27/27 live tests green in one clean run, zero modifications to existing code, every credential boundary exercised as a real OS/Postgres denial, every divergence class forced and correctly classified, and the two completeness layers (feed comparison + operator-independent ICC) both proven to catch what the other cannot. The honest gaps — retention pruning at scale, per-customer feed scoping, service hardening, and the pre-existing suite-isolation defect — are named in §3, §5, and §6 rather than papered over. The next step is the full-suite red-team that §6 sets up.
