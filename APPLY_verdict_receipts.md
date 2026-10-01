# Apply document: verdict receipts (TACK Layer 5, Blueprint B)

Delivered on branch `claude/verdict-receipts-governance-72o5xt` of
`wking53214/sentinel_os`, one commit per numbered step (2.0 to 2.10), on top
of `wking53214/CNS` at its 1.4.0 release commit (`3b465db`). The CNS half of
the run (Phase 1, steps 1.1 to 1.6) moved the composition layer out of the
shapes package and released 1.4.0; nothing in `cns.gate` changed.

This document is the delivery record. Take "this change" throughout as the
branch.

---

## 1. What changed, in plain words

Every governance decision the ledger writes is now a receipt an outside
auditor can verify with no access to the running system: it is bound to
exactly what was judged, signed over its content with a key the agent does
not hold, hash-chained as before, and anchored outside the database so a cut
tail is detectable. One command checks an export offline.

Files, by role:

- **The shared contract, `sentinel_os/canonical_fields.py`.** Two entries in
  `OPTIONAL_HASHED_FIELDS`, one line each, present when truthy:
  `subject_digest` and `shuffle_seed`. One fixed canonical form for the new
  `attestation_policy` record kind, built by `attestation_policy_canonical`
  and imported by the writer, the primary verifier and the witness. Nothing
  else in the file moved.
- **The writer, `governance/ledger_postgres.py`.** `append_decision` computes
  `subject_digest` with `cns.gate.subject_digest` over the decision's stored
  `input_data`, before any row is written, and refuses the write when the
  CNS cannot encode the content (NaN, infinities, unsupported types). The
  gate identity (cassette name, position `omega`, CNS outcome) rides inside
  the hashed `output` mapping; no column was added for it. Signatures on
  decision rows are `abv3`, covering the content pre-hash. At boot, under
  enforcement, the ledger appends the `attestation_policy` marker once per
  key fingerprint and proves the anchor location writable. After every
  successful append it hands the witness the head to anchor. Two hashed
  columns were added, `subject_digest` and `shuffle_seed`, both entering the
  hash through the shared contract; no unhashed column was added.
- **The witness, `twin_custody.py`.** Recomputes the subject binding with the
  same CNS function, re-derives a present seed, verifies signatures (for the
  first time), walks a whole chain (`verify_rows`) and owns the head anchor:
  build, write, read, verify, compare. The two new columns are shipped.
- **The attestation module, `governance/authorized_by_attestation.py`.**
  `abv3` beside `abv2`; the shuffle seed rule (`derive_shuffle_seed`,
  `verify_shuffle_seed`); enforcement on by default with one dev-only
  override.
- **The primary verifier, `verify_chain`.** Reports the same findings the
  witness does, in the same words.
- **The offline command, `tools/verify_receipts.py`**, and its input,
  `sentinel_os/scripts/export_ledger.py`.
- **Deployment.** `docker-compose.yml` requires the attestation key and
  mounts the anchor volume on the worker only. `DEPLOYMENT.md`,
  `TROUBLESHOOTING.md`, `STACK.md` updated. `requirements.txt` pins cns at
  the 1.4.0 release commit.
- **Tests.** `Tests/test_verdict_receipts.py` (new, one section per step,
  acceptance tests a to j named by letter), `Tests/fixtures/
  ledger_export_pre_receipts.json` (a ledger written by the writer as it
  stood before this change). Edits to existing tests are listed in section 5.

## 2. The six violation kinds

The offline verifier prints exactly one of these, then the first failing row
id and the reason. `verify_chain` and the witness use the same words.

| kind | what it means in plain words |
|---|---|
| `TAMPERED` | The row's bytes are not what was hashed, or the row does not link to the one before it, or a signature does not match the content it claims to cover. Something in the row was changed after it was written. |
| `TRANSPLANTED` | The verdict was moved onto content it was not issued for. The row's `subject_digest` is not the CNS digest of the row's own input data, even though the hash chain may have been recomputed to look clean. |
| `SEED_FORGED` | The agent, not the server, chose the order of the validation gates. The row carries a `shuffle_seed` that does not re-derive from the previous hash and record kind under the ledger's key (or no key is held to re-derive it, which fails closed). Today no row carries a seed. |
| `TRUNCATED` | The chain is shorter than its anchor says, or the hash at the anchored position differs from the anchored head, or the anchor itself does not verify. The tail was cut, or the chain rebuilt, after the head was sealed. Rows appended after the seal are expected and are not this. |
| `UNATTESTED` | A row names an accountable party and carries no signature, after the chain's `attestation_policy` marker said attestation was enforced; or the signature was made by a key the auditor does not trust. Before the marker, an unsigned claim is what it always was: a claim, not a violation. |
| `VERIFIED` | None of the above, over every row and the anchor. Exit code zero, only here. |

## 3. The two reserved fields

- **`subject_digest`**: populated now, on every `governance_decision` row
  the writer produces, from the CNS canonical digest of the row's stored
  input data. Rows written before this change do not carry it and are not
  judged by it.
- **`shuffle_seed`**: reserved for TACK Layer 1, the MTD sequencer. It is
  the server-derived seed that fixed the order of non-dependent validation
  gates for the row: an HMAC-SHA256 over the previous row's hash and the
  record kind, keyed with the ledger attestation key, rendered as hex
  (`authorized_by_attestation.derive_shuffle_seed`). Absent on every row
  written by a chain that was not shuffled, which today is every row; the
  writer never reads it from the record or the request. Layer 1 will
  populate it from the helper, and the witness already re-derives it. The
  ledger is not migrated twice.

## 4. Migration story: none required

Every new field is present-when-truthy in the canonical form, so a row
written before this change hashes to the bytes it hashed to then. The two
new columns are nullable and added by the existing idempotent migration
block; no backfill. The `attestation_policy` marker is a new row appended at
boot, after existing rows; rows before it are untouched and unsigned claims
among them are not violations. Acceptance test (f) verifies a ledger export
taken with the pre-change writer, with no marker and none of the new
fields, to `VERIFIED` through the offline command; that export is checked
in as a fixture and will keep failing the day an old row's bytes move.

Signatures: `abv2` rows verify exactly as before; decision rows written from
now on carry `abv3`. Other record kinds that carry `authorized_by`
(contract rows, regulatory events, supersessions, selections) still sign
with `abv2`, over the name and chain position only; moving them to `abv3`
is a per-writer change left for a later step so this run stayed narrow.

Enforcement: on by default. A deployment that relied on the old opt-in
default and has no key will refuse to start; it needs
`ICEBERG_LEDGER_ATTESTATION_KEY` (or, on a developer's machine only,
`ICEBERG_LEDGER_ATTESTATION_DEV_OVERRIDE=1`). The compose profile stops
before starting anything if the key is unset.

## 5. Existing tests edited, and why

Each of these asserted the pre-change behaviour that a numbered step
deliberately changed. None was edited to hide a failure.

- `Tests/test_human_selection_ledger.py`: one assertion compared the stored
  decision output to the caller's dict by equality; the gate identity now
  rides inside that output (step 2.1). It compares the caller's keys and
  checks the gate entry.
- `Tests/test_authorized_by_attestation.py`: three tests asserted the
  `abv2` form on decision rows (step 2.4); they assert `abv3` and verify
  through the witness. Three tests meant "enforcement off" by leaving the
  flag unset (step 2.7); they set the dev override.

## 6. What this does not do, stated plainly

- The attestation is HMAC, so verifying it needs the key, not only its
  fingerprint. The fingerprint list says which keys an auditor trusts; the
  key itself is handed over out of band. The offline command refuses to
  print a verdict it could not check.
- The attestation key is service-level (locked decision D1): every holder is
  indistinguishable, and a leaked key forges everything under it. The head
  anchor is signed with the same key and shares that boundary. In the
  compose profile the process that writes rows also holds the key and
  writes the anchor; what the anchor defeats is a change made through the
  database (an owner role, a restore, a rewritten table), which cannot
  reach the anchor and does not hold the key.
- Nothing shuffles gates yet. The seed field, the rule and the witness
  check exist so that Layer 1 arrives without a second ledger change.
