# Twin Transport Failure Taxonomy v1

**Date:** July 18, 2026
**Scope:** every transport/integrity failure mode the twin sync path can encounter, the existing `Reason` it maps to, its retry disposition, and the live test that forces it.
**Principle:** this taxonomy lists only failure modes that are **actually forced and observed** in `test_twin_live.py`. No mode appears here on the strength of "it should happen"; each row cites the test that made it happen against real services. Modes that are excluded *by construction* are named in §3 with the reason they cannot occur, not left as silent gaps.

The twin introduces **no new failure vocabulary**. It reuses the V12 `TransmissionQueue`'s existing `Reason` enum, exactly as `sentinel_worker` does, so a struggling replica backs a queue up the same way a struggling worker already does — one taxonomy, one dead-letter concept, one operator playbook.

---

## 1. Delivery failures (sync worker → receiver)

| # | Failure forced | Mapped `Reason` | Retryable | Terminal disposition | Proven by |
|---|---|---|---|---|---|
| T-1 | Receiver process down (connection refused) | `SERVICE_INTERRUPTION` | yes | retries with backoff; delivers when receiver returns | `test_offline_replica_backs_up_then_recovers` |
| T-2 | Receiver accepts then never responds (read timeout / partition mid-flight) | `NETWORK_LATENCY` | yes | retries; bounded by request timeout | `test_partition_midflight_maps_to_network_latency` |
| T-3 | Torn / truncated envelope rejected `422` | `DATA_CORRUPTION` | **no** | dead-letters immediately; operator `requeue_from_dlq` + clean re-ship delivers | `test_torn_delivery_dead_letters_then_requeues` |
| T-4 | Immutability conflict `409` (delivery would rewrite a stored row) | `DATA_CORRUPTION` | **no** | dead-letters; a delivery that would rewrite history is never retried into place | `test_duplicate_delivery_is_idempotent` (409 path) |
| T-5 | Bad ship token `401` | `UNCLASSIFIED` | **no** | dead-letters; an operator-misconfiguration, not a transport state | mapping in `twin_sync_worker.handle_one` (asserted via the 401 branch) |
| T-6 | Receiver `5xx` | `SERVICE_INTERRUPTION` | yes | retries with backoff | mapping in `twin_sync_worker.handle_one` |

**Why the retryable/non-retryable split is drawn here.** A failure that a later attempt could plausibly succeed at (the receiver is down, slow, or briefly erroring) is retryable. A failure that means *this exact delivery is wrong* (`422` structural, `409` would-rewrite, `401` bad credential) is non-retryable — retrying it changes nothing and only delays the operator noticing. `DATA_CORRUPTION` for the torn case is deliberate: the corruption is in the delivery, and the correct recovery is a fresh seal from the shipper (which the requeue-then-reship test exercises end to end), not a blind re-send of the same bad bytes.

---

## 2. Integrity findings (detector verdicts)

These are not transport failures — they are what the detector reports when the replica and primary disagree. Listed here because the taxonomy is what an operator consults when *something* is wrong, and "wrong on the wire" and "wrong on comparison" share that entry point.

| # | Condition forced | Verdict / sub-cause | Proven by |
|---|---|---|---|
| D-1 | Ciphertext byte-flipped at rest on the replica | DIVERGE / `envelope_unopenable` | `test_tamper_distinct_from_omission` |
| D-2 | Clear `current_hash` edited on the replica | DIVERGE / `clear_hash_mismatch` | `test_tamper_distinct_from_omission` |
| D-3 | Row shipped but decrypted payload wouldn't recompute | DIVERGE / `payload_hash_mismatch` | recompute path, `test_recompute_catches_field_edit` (custody suite) |
| D-4 | Row withheld at ship time, exists on primary | MISSING / `present_on_primary_not_replica` | `test_forced_omission_caught_by_icc` |
| D-5 | Row the customer submitted, absent from primary entirely | MISSING / `absent_everywhere` (ICC) | `test_forced_omission_caught_by_icc` (ghost case) |
| D-6 | Row present on replica, gone from primary (wipe) | EXTRA / `extra_on_replica_absent_on_primary` | `test_wipe_detection_via_extras` |
| D-7 | Two corruptions at once, distinguished from each other and from a clean row | mixed D-1 + D-2, one MATCH survives | `test_tamper_distinct_from_omission` (asserts both sub-causes **and** `match == 1`) |

The D-1-vs-D-4 distinction is the sharpest one the taxonomy makes: **tamper is not omission.** A flipped ciphertext (the row is there but altered) and a dropped row (the row is gone) produce different verdicts with different sub-causes, and the test asserts they don't blur into a generic "something's off."

---

## 3. Modes excluded by construction (named, not hidden)

| Mode | Why it cannot occur in DAP v1 |
|---|---|
| **Clock-skew divergence** | The detector consults no wall clock on the entries — ordering is by `primary_id` + hash linkage only. Forced-tested by rewriting `received_at` five years into the future and observing the verdict unchanged (`test_sla_pending_vs_missing_and_clock_skew_immunity`). Not a failure mode; a non-mode by design. |
| **Double-enqueue on shipper retry** | `job_id = "{replica_id}|{primary_id}"` and the queue dedups on `job_id`. A re-ship of the same row cannot create a second job. |
| **Duplicate stored entry** | `(replica_id, primary_id)` is unique in the receiver; identical re-delivery returns `duplicate`, not a second row. |
| **Out-of-order corruption** | Arrival order is not used for storage or chain reconstruction; delivering 3,2,1 yields the same chain as 1,2,3 (`test_out_of_order_delivery_reconstructs_chain`). |
| **Silent job loss on worker crash mid-delivery** | Inherited from the existing queue's lease/reap machinery — an abandoned lease is reclaimed, not lost. Not re-proven here; it is the base queue's own covered property. |

---

## 4. Operator playbook (one path, because one taxonomy)

1. **Retryable delivery failure (T-1, T-2, T-6):** no action — backoff handles it; confirm recovery via `GET /replica/{id}/head`.
2. **Dead-lettered delivery (T-3, T-4, T-5):** inspect `error_trail(job_id)` for the `Reason`; for `422` torn (T-3), `requeue_from_dlq` then re-ship; for `401` (T-5), fix the ship token then re-ship; `409` (T-4) means the replica already holds a *different* row at that id — investigate before forcing.
3. **Integrity finding (D-1…D-7):** this is a divergence, not a transport hiccup — escalate per the attestation report, do not "retry." The probe (`twin_probe.py`) produces the machine-readable finding with exact counts.
