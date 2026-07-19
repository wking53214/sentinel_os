# Divergence Attestation Protocol (DAP) v1 — Specification

**Status:** reference specification, v1
**Date:** July 18, 2026
**Scope:** the customer-controlled disaster-recovery witness replica ("the twin") for the Sentinel OS V12 governance ledger
**Reference implementation:** `twin_custody.py`, `twin_receiver.py`, `twin_shipper.py`, `twin_sync_worker.py`, `twin_detector.py`, `twin_custodian.py`, `twin_migrate.py`, `twin_probe.py` in this repository, with live conformance tests in `test_twin_custody.py` and `test_twin_live.py`.

This document is the normative part. An independent implementation built from this spec alone, run against the same inputs, must reach the same verdicts. The reference implementation is one realization of it; the wire formats, canonicalization rules, verdict definitions, and the two completeness layers below are the contract.

---

## 1. Purpose and threat model

The Sentinel V12 ledger is a hash-chained, tamper-evident record of governance decisions. On its own it answers "has this ledger been altered since it was written?" It does **not**, on its own, answer a question a regulator or a customer's counsel will eventually ask: *"can the operator of the ledger show me that nothing was quietly dropped, and can they prove it with something they do not solely control?"*

DAP addresses that by shipping every committed ledger row, encrypted, to a replica held under **the customer's** credentials and (optionally) a **neutral custodian's** key. The replica is an external witness. Three properties follow:

1. **Confidentiality from the operator.** Replica payloads are sealed to a key the operator (Sentinel) never holds. Sentinel ships ciphertext and cannot read it back — not from the wire, not from the customer's key file, not from the customer's database.
2. **Divergence is detectable and attributable.** A customer or regulator can compare the replica against the primary and classify every difference: altered (diverge), dropped (missing), or present-on-replica-but-gone-from-primary (extra / wipe evidence). Where custody is neutral, every decryption is logged and signed, so *who read what, and who tried,* is a checked statement.
3. **Completeness has an operator-independent check.** Whether the primary ledger dropped a row *before* it was ever chained is invisible to any check that starts from the ledger. DAP adds a second layer — the Independent Completeness Cross-check (§6.4) — anchored in the customer's own submission record, created outside anything Sentinel can edit.

**What DAP v1 does not claim.** It does not make decision-time correctness assertions (whether a governance decision was *right*), it does not cover model-identity or content-addressed-cassette provenance (those are separate W-items — see `twin_w_reconciliation_v1.md`), and it does not prevent a wipe — it makes a wipe *evident after the fact* to a party holding a replica.

---

## 2. Canonicalization (DAP-2)

Two distinct canonical forms appear in DAP. They must not be conflated.

### 2.1 Protocol canonical JSON

Everything DAP itself signs or binds (AAD, custody-log payloads, custodian audit records, submission receipts) uses:

```
json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
```

Compact separators, sorted keys, UTF-8. This is `canonical_json()` in the reference implementation.

### 2.2 Ledger canonical form (recomputation)

To confirm a decrypted replica payload really is the preimage of the primary's `current_hash`, an implementation recomputes that hash. The recomputation must reproduce, **byte for byte**, the canonicalization the Sentinel ledger used at append time. That form uses Python's **default** `json.dumps` separators (not the compact form above):

```
hashlib.sha256(json.dumps(canonical_entry, sort_keys=True, default=str).encode()).hexdigest()
```

There are two shapes of `canonical_entry`, selected by `record_kind`:

- **Base rows** (`ledger_postgres.append`): keys `action_type, node, previous_value, applied_value, reason, data, previous_hash`.
- **Governance decisions** (`ledger_postgres.append_decision`, `record_kind = "governance_decision"`): keys `record_kind, action_type, node, cassette_version, input_data, policy_parameters, reasoning, output, previous_value, applied_value, parameter_changed, previous_hash`, plus `cassette_hash` **only if** a cassette snapshot was captured.

The mapping from stored columns to these keys (e.g. the stored `reason` column supplies `reasoning` for decisions; `decision_output` supplies `output`) is implemented in `recompute_current_hash()`. Conformance requires matching it exactly; the reference test `test_recompute_matches_every_live_ledger_row` pins it against real ledger rows.

> **Note on `call_sid`.** Governance-decision rows carry `call_sid` both as a column and inside `input_data`. Base rows leave the column null and carry any identifier inside `data`. Implementations resolving a row's identifier must read the column first and fall back to the row JSON.

---

## 3. Envelope format (DAP-3)

Each replicated row is sealed into an envelope using standard primitives only — X25519 ECDH, HKDF-SHA256, AES-256-GCM — composed, never hand-rolled.

### 3.1 Sealing

```
ephemeral X25519 keypair  (per seal)
shared   = ECDH(ephemeral_priv, recipient_pub)
key(32)  = HKDF-SHA256(shared, salt=None, info = b"twin-dap-v1|" + sha256(AAD))
ct       = AES-256-GCM(key, nonce(96-bit random), plaintext, associated_data = AAD)
```

The payload cipher is AES-256-GCM. The standing statement "AES-256 at rest; the customer holds the keys; Sentinel holds no key material" is therefore literally true under both custody models.

### 3.2 Envelope object

```json
{
  "v": 1,
  "alg": "X25519+HKDF-SHA256+AES-256-GCM",
  "epk":   "<base64 raw 32-byte ephemeral public key>",
  "nonce": "<base64 12 bytes>",
  "ct":    "<base64 ciphertext incl. 16-byte GCM tag>",
  "recipient_fp": "<sha256(recipient_pub)[:16] hex>"
}
```

### 3.3 AAD slot-binding — the load-bearing property

The Associated Data is the protocol-canonical JSON of exactly:

```json
{"replica_id": "<id>", "primary_id": <int>, "current_hash": "<hex>"}
```

Because the AAD is authenticated by GCM **and** folded into the HKDF `info`, a sealed envelope opens **only** in its own slot. Sentinel cannot move a validly sealed blob to a different `primary_id`, a different replica, or a different hash position without the open failing. This is what makes "cut-and-paste" or "replay an old sealed row into a new slot" attacks fail closed. Verified by `test_slot_binding_prevents_envelope_relocation` and the three wrong-AAD variants in the live suite's deep-verification path.

### 3.4 Opening

An implementation MUST reject (as a hard error, not a silent empty result):

- version/alg mismatch,
- wrong recipient key,
- any AAD field altered (wrong slot),
- any ciphertext or tag tampering (GCM failure).

---

## 4. Receiver API (DAP-4)

The receiver runs under the customer's credentials against a database the operator's roles cannot connect to. All routes are under `/replica/{replica_id}`.

| Method + path | Auth | Behavior |
|---|---|---|
| `POST /register` | none¹ | Create replica metadata (custody model, recipient pub + fp, customer signing pub, ship token, SLA `max_lag_seconds`, `retention_days`, `is_primary_evidence`). Idempotent. |
| `GET  /meta` | none | Metadata minus the ship token. |
| `POST /entries` | `Bearer <ship_token>` | Store one sealed entry. **Append-only + idempotent** — see §4.1. |
| `GET  /head` | none | `{max_primary_id, count}`. |
| `GET  /entries?after_id=&limit=` | none | Entries ordered by `primary_id`. |
| `POST /custody-event` | none¹ | Append a customer-signed custody event to the hash-chained custody log — see §5. |
| `GET  /custody-log` | none | The full custody log for regulator verification. |

¹ Registration, custody-event, and the read routes are unauthenticated in the reference implementation, which binds `127.0.0.1`; the database credential is the trust boundary. A production deployment fronts these with the customer's own authentication (§4.6). The `/entries` ship-token check is always enforced because it is what stops an unauthorized party writing history into the replica.

### 4.1 Append-only and idempotency

`(replica_id, primary_id)` is unique. On `POST /entries`:

- **New** `primary_id` → `201`-equivalent `{"status": "stored"}`.
- **Identical** re-delivery (same `previous_hash`, `current_hash`, `call_sid`, and byte-identical envelope) → `200 {"status": "duplicate"}`. This makes the transport safely at-least-once.
- **Conflicting** re-delivery (same `primary_id`, any different content) → **`409`, refused**. The receiver never mutates a stored entry. History already in the customer's custody is not the shipper's to rewrite.

### 4.2 Structural validation

An envelope missing any of `v, alg, epk, nonce, ct, recipient_fp`, or whose `epk`/`nonce`/`ct` are not valid base64 of the required lengths (`epk` 32 bytes, `nonce` 12 bytes, `ct` ≥ 17 bytes), is refused **`422` at the door**. A torn or partial delivery surfaces immediately rather than resting in storage. Verified by `test_structural_envelope_validation` and the torn-delivery live test.

### 4.3 Order independence

Entries may arrive in any order. The receiver stores by `primary_id`; chain order is reconstructed at verification time from `primary_id` + hash linkage. Wall-clock arrival time is never used for ordering. Verified by `test_out_of_order_delivery_reconstructs_chain` (deliver 3,2,1 → chain intact).

### 4.6 Deployment note

The reference receiver is the customer's process and DB boundary, not a hardened public service. Production customers put their own authN/Z in front of registration and reads, use durable storage, and may co-locate the receiver on operator hardware — the credential and key boundaries, not the rack, are what make the replica the customer's.

---

## 5. Custody log contract (DAP-5-log)

Each replica carries a hash-chained, customer-signed log of custody events (creation, evidence designation, rotation, migration A→D).

The **caller** (customer tooling) signs the protocol-canonical content payload:

```json
{"replica_id": "...", "event": "...", "detail": {...}, "actor": "..."}
```

with its Ed25519 signing key, and supplies `signer_pub`. The **receiver** assigns `seq` (monotonic per replica) and `prev_hash` (previous entry's `curr_hash`, or `"genesis"`), computes `curr_hash = sha256(canonical_json({replica_id, seq, event, detail, actor, prev_hash}))`, and stores the record. Optimistic-concurrency: a caller may pass an expected `seq`/`prev_hash`; a mismatch returns `409` with the expected values.

A regulator verifies the log by (a) walking the chain — each `prev_hash` equals the prior `curr_hash`, each `curr_hash` recomputes — and (b) verifying `signer_pub` over the content payload for every event. Implemented in `twin_probe.verify_custody_log`.

---

## 6. Divergence detection and verdicts (DAP-6)

Detection runs entirely on customer-side inputs: the replica (via receiver API), the **primary hash feed** (a read-only credential exposing `id, call_sid, previous_hash, current_hash, timestamp` — hashes and identifiers, never an operator-authored verdict), the customer's **submission record** (§6.4), and optionally decryption authority for deep verification.

### 6.1 The covered window — how a witness is scoped

A replica witnesses a **defined window** of the primary stream. It can only be held accountable for rows *within* that window. The window is a `(lo, hi)` range of `primary_id`:

- If the caller supplies a range, that is the window.
- Otherwise the window defaults to the replica's own `min..max` synced `primary_id` span.

Rows **outside** the window are not "missing" — they are not-yet-covered (before the replica's first synced row) or future (after its last). This is deliberate and it is why the completeness layer of §6.4 exists: an omission at the very **edge** of the stream falls outside the window by construction, and is caught not by the feed comparison but by the operator-independent submission-record cross-check. Within-window gaps are caught by the feed comparison; edge and pre-ledger omissions are caught by the ICC. The two layers together close the gap either layer alone would leave.

The covered window is reported in every detection result as `covered_window: {lo, hi}`.

### 6.2 Per-entry verdicts

Evaluated over the scoped feed:

- **MATCH** — present on both sides; `previous_hash` and `current_hash` agree; and, when deep verification is enabled, the envelope opens and the decrypted payload recomputes to `current_hash`.
- **DIVERGE** — present on both sides but different. Sub-cause:
  - `clear_hash_mismatch` — the replica's clear `previous_hash`/`current_hash` disagree with the primary.
  - `envelope_unopenable` — clear hashes agree but the sealed payload fails to open (tampered ciphertext, wrong/*rotated-away* key).
  - `payload_hash_mismatch` — the envelope opens but the decrypted row does not recompute to the stored hash.
- **MISSING** — expected within the window but absent from the replica after the SLA. Sub-cause:
  - `present_on_primary_not_replica` — the row exists on the primary (withheld or lagging ship).
  - `absent_everywhere` — from the ICC: the customer submitted it, the primary never shows it (dropped before the ledger — the gravest case).

Plus two non-verdict / anomaly classes:

- **PENDING** — absent but younger than the replica's `max_lag_seconds` SLA. Not a finding.
- **EXTRA** — present on the replica but absent from the primary within the window. This is the **wipe / rewrite evidence class**: the customer's copy holds history the primary no longer admits to. Sub-cause `extra_on_replica_absent_on_primary`. Verified by `test_wipe_detection_via_extras`.

### 6.3 Chain-order check

Over the replica's own entries ordered by `primary_id`: for consecutive rows whose ids differ by exactly 1, `current.previous_hash` must equal `prior.current_hash`. A break is reported as `replica_linkage_break`. (A *gap* in ids is not a linkage break — a dropped middle row leaves ids 90 and 92 adjacent with no linkage assertion between them; the gap is caught as MISSING, not as a chain break.) Clocks are never consulted, so clock skew is not a divergence source — verified by rewriting `received_at` five years into the future and observing the verdict unchanged.

### 6.4 Independent Completeness Cross-check (ICC) — the named clause

**This is DAP's distinctive contribution.** Any check that begins from the ledger cannot detect a row that was dropped *before* it was chained — there is nothing in the chain to be missing. The ICC anchors completeness in a source the operator does not control: the customer's **own submission record**, a log of every `202`-accepted submission (`{"sid" | "job_id", "t"}`) written by the customer's submitter at submission time.

For each submission-record entry whose `sid` is not on the replica:

- if the `sid` **is** on the primary feed → `present_on_primary_not_replica` (a ship/replication gap),
- if the `sid` is **not** on the primary feed → `absent_everywhere` (dropped before the ledger).

Age vs SLA decides PENDING vs MISSING as elsewhere. The ICC is what lets a customer state "every call I accepted is accounted for" rather than only "the ledger I was shown is internally consistent." Verified by `test_forced_omission_caught_by_icc`, including the `absent_everywhere` (ghost submission) case.

---

## 7. Custody model D — neutral custodian (DAP-7)

Under model D the recipient key belongs to a neutral custodian process running under its own credentials. Decryption happens only through the custodian's API; every attempt — granted or refused — is logged and signed.

### 7.1 Endpoints

| Method + path | Behavior |
|---|---|
| `GET  /public-keys` | Recipient public key + fingerprint, and the custodian's log-signing public key. |
| `POST /register-replica` | Register a replica's customer signing pub (authorizes future decrypt requests). |
| `POST /decrypt` | Decrypt one envelope **iff** the request carries the customer's valid Ed25519 signature over `{replica_id, primary_id, nonce, requester}`, the replica is registered, and the nonce is unused. Grants and refusals are both logged with the claimed `requester`. |
| `GET  /audit-log` | The hash-chained, custodian-signed audit log. |

### 7.2 Authorization and replay

A decrypt requires the customer's signature over the auth payload. Nonces are single-use — a replayed authorization is refused and logged. A request with a bad signature is refused `403` **and still logged**, with the requester it claimed to be. This is the attribution layer: "the operator requested zero decrypts in this window" becomes a signed, independently verifiable statement, and "the operator *tried* and was refused" is equally on the record. Verified by `test_custodian_attribution_grants_and_refusals` (valid grant logged as `customer-audit`; forged request claiming `sentinel` refused and logged).

### 7.3 Audit-log verification

`verify_audit_log(events, log_sign_pub)`: walk the chain (`prev_hash`/`curr_hash`) and verify the custodian's signature on every record. Importable and used by the probe.

---

## 8. Custody migration (DAP-8)

Migration (e.g. A→D) re-seals the **seal layer only**. It runs under the customer's credentials against the customer's own replica database — the receiver deliberately exposes no envelope-mutation endpoint, so the operator cannot perform it over the wire.

For each stored entry: open with the old key (AAD = the entry's own slot), re-seal the identical plaintext to the new recipient key **using the same AAD slot**. Because the slot is unchanged, deep verification holds across the migration. Any single open failure **aborts** the whole migration (no partial re-encryption). On success, replica metadata is updated and a customer-signed `custody_migration` event (old fp, new fp, entry count) is appended to the custody log. After re-sealing, the old key no longer opens any stored envelope — the customer's final step is to retire it. Verified by `test_custody_migration_A_to_D`: old key fails afterward, custodian opens and deep-verifies, signed event present.

---

## 9. Conformance probe (DAP-9)

The probe is the regulator's one-command red button, run on customer-side inputs with no operator cooperation:

```
twin_probe.py --receiver-url URL --replica-id ID --feed-dsn DSN \
  [--submission-record FILE] [--sla-seconds N] \
  [--key-file KEY | --custodian-url URL --sign-key-file KEY]
```

It emits a single JSON object and exits `0` iff conformant, `2` otherwise:

```json
{
  "probe": "DAP-9", "dap_version": 1, "conformant": <bool>,
  "detection": { ...§6 result, incl. counts and covered_window... },
  "custody_log": {"ok": <bool>, "events": <int>, "kinds": [...]},
  "custodian": null | {
    "log_verified": {"ok": <bool>, "records": <int>},
    "decrypt_grants_by_requester":   {"<who>": <n>},
    "decrypt_refusals_by_requester": {"<who>": <n>}
  }
}
```

`conformant` is true iff detection is CLEAN, the custody log verifies, and (model D) the custodian audit log verifies. Every field the probe reports is derived from the documented DAP interfaces, so an independent probe built from this spec must reach the same verdict on the same inputs. Verified by `test_probe_clean_and_seeded_findings` (clean → conformant, exit 0; one tamper + one omission → exact counts, exit 2).

---

## 10. Transport (DAP-5)

Shipping reuses the existing V12 `TransmissionQueue` unchanged:

- The **shipper** tails committed ledger rows (read-only), seals one job per replica target, and enqueues on the `twin_sync` queue with `job_id = "{replica_id}|{primary_id}"` (idempotent). A Redis cursor per replica advances only after enqueue (at-least-once). The shipper does **no** writes to the primary path.
- The **sync worker** claims jobs and delivers over HTTP, mapping transport failures onto the existing `Reason` vocabulary (see `twin_failure_taxonomy_v1.md`): connect-refused → `SERVICE_INTERRUPTION` (retryable); read-timeout/partition → `NETWORK_LATENCY` (retryable); receiver `422`/`409` → `DATA_CORRUPTION` (dead-letter); `401` → `UNCLASSIFIED` (dead-letter); `5xx` → `SERVICE_INTERRUPTION` (retryable).

Because shipper and worker are out-of-band, a dead replica, a full sync queue, or an offline receiver slows nothing on the primary path. Verified by `test_never_blocks_primary` (primary appends stay fast with the twin queue backed up against a dead receiver).

---

## Appendix A — verdict quick reference

| Verdict | Meaning | Sub-causes |
|---|---|---|
| MATCH | identical, deep-verified | — |
| DIVERGE | present both, different | `clear_hash_mismatch`, `envelope_unopenable`, `payload_hash_mismatch` |
| MISSING | expected in-window, absent after SLA | `present_on_primary_not_replica`, `absent_everywhere` (ICC) |
| PENDING | absent, within SLA | — |
| EXTRA | on replica, gone from primary | `extra_on_replica_absent_on_primary` (wipe evidence) |

## Appendix B — the two completeness layers

| Layer | Anchored in | Catches | Blind to |
|---|---|---|---|
| Feed comparison (§6.2) | primary hash feed | within-window alteration and drop | rows dropped before the ledger; edge omissions outside the window |
| ICC (§6.4) | customer submission record | any submitted `sid` absent from the replica, incl. pre-ledger drops | anything the customer never recorded at submission |

Neither alone is sufficient; together they close the gap.
