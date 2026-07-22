# Phase 2 Migration Notes

**Scope:** deploying the Phase-2 changes (Items 1–7) onto an existing production
Sentinel database with an already-populated `ledger_entries` table.

**Bottom line:** deployable online, no downtime, no backfill, no chain break.
`verify_chain()` continues to pass on all pre-existing rows. Verified live against
real Postgres 16: full suite 161/161, with an explicit test that a plain
pre-Phase-2-shaped decision row still recomputes byte-identical.

---

## 1. Schema changes

All new columns are **nullable** and added with `ADD COLUMN IF NOT EXISTS`:

| Column | Type | Item | Purpose |
|--------|------|------|---------|
| `cassette_code_hash` | `VARCHAR(64)` | 3 | hash of the cassette's decision code |
| `model_identity` | `VARCHAR(120)` | 5 | model that produced the decision (`response.model`) |
| `authorized_by` | `VARCHAR(120)` | 7 | authorizing role/service identity |
| `supersedes_id` | `INTEGER` | 6 | row ID a supersession points at |
| `supersedes_hash` | `VARCHAR(64)` | 6 | `current_hash` of the superseded row |

Plus three non-unique indexes (`idx_model_identity`, `idx_authorized_by`,
`idx_supersedes_id`).

`ADD COLUMN ... NULL` with no default is a metadata-only change in PostgreSQL —
it does **not** rewrite the table and does **not** take a long lock, so it is safe
on a large populated `ledger_entries`. The index builds are the only part that
scans; for a very large table, create them `CONCURRENTLY` out-of-band rather than
inside the migration transaction (the shipped DDL uses plain `CREATE INDEX IF NOT
EXISTS` for simplicity — swap to `CONCURRENTLY` if your table is big enough that a
brief index-build lock matters).

The migration runs automatically on ledger init (the `ALTER TABLE ... IF NOT
EXISTS` block in `PostgreSQLLedger`), so a normal deploy applies it. It is
idempotent — re-running is a no-op.

---

## 2. Why old rows stay verifiable (the core guarantee)

Every new field is added to the SHA-256 canonical form **only when present and
truthy**, through the shared `canonical_fields.apply_optional_hashed_fields()`
contract. Because `json.dumps(sort_keys=True)` omits absent keys, a row written
before these fields existed — where all five columns are `NULL` — produces
**exactly the same canonical bytes it produced before Phase 2**, and therefore
the same hash.

This is the identical mechanism Phase 1 already used for `cassette_hash`
(`if cassette_hash:`). Phase 2 generalizes it to one contract used by **all three**
hash-recompute sites:

1. the writer (`ledger_postgres.append_decision`)
2. the primary verifier (`ledger_postgres.verify_chain`)
3. the customer witness (`twin_custody.recompute_current_hash`)

All three import the same contract, so they cannot drift on which fields enter the
hash. (Note for reviewers: there are **three** recompute sites, not two — the
primary `verify_chain` is easy to overlook. Any future hashed field must be added
to the shared contract, which updates all three at once.)

**Consequence:** no backfill is required or wanted. Do **not** attempt to
populate `model_identity` / `authorized_by` / `cassette_code_hash` on historical
rows — doing so would change their canonical form and **break** their stored hash.
Historical rows correctly carry `NULL` (the honest statement: "this predates the
field"). New rows carry the values.

---

## 3. New record kinds

Two new `record_kind` values append to the same chain:

- `cassette_binding` (Item 2) — commits a `cassette_version` → (`cassette_hash`,
  `cassette_code_hash`, `authorized_by`) binding.
- `decision_supersession` (Item 6) — references a prior decision by ID and hash.

Both are ordinary chain rows (they take the same advisory lock and link
`previous_hash` → `current_hash`), and all three recompute sites understand them.
Existing `governance_decision` and legacy base rows are unchanged. A chain mixing
all kinds verifies end-to-end (tested live).

No migration action is needed for these — they simply become available. Nothing
in the existing chain references them retroactively.

---

## 4. Application-layer changes (no DB impact)

- **Governor (`claude_governance_api.py`):** all four decision methods now use the
  structural injection defense (system-role instruction + escaped delimited data)
  and capture `response.model`. Two methods (`decide_staffing_adjustment`,
  `decide_queue_reordering`) that previously could raise on a missing client — and
  previously emitted *invented* fallback outcomes on parse failure — now fail
  closed like the others. **This is a behavior change to be aware of:** a parse
  failure or missing client on those two paths now yields an ungoverned/no-change
  result (`recommended_agents: None` / `proposed_order: None`) instead of a
  fabricated number. That is the correct fail-closed behavior, but if anything
  downstream depended on the old fallback *producing* a value, it will now see
  `None`. Nothing in the shipped harness did.

- **Harness (`production_harness.py`):** threads `model_identity`, `authorized_by`
  (default `"harness:production"`, override via `config["authorized_by"]`), and
  `cassette_code_hash` (via the fail-closed `_safe_code_hash`) into each decision
  record. If code-hash computation fails for any reason it logs and passes `None`
  — a decision is never blocked by hashing.

---

## 5. Deploy checklist

1. Deploy the code. Ledger init applies the additive schema migration
   automatically (idempotent).
2. For a very large `ledger_entries`, pre-create the three indexes
   `CONCURRENTLY` before/after rather than relying on the in-transaction
   `CREATE INDEX` (optional; only matters at scale).
3. Run `verify_chain()` — it passes on all pre-existing rows (they recompute
   unchanged) and on any new rows.
4. Do **not** backfill the new columns on historical rows.
5. (Optional, recommended) set `ICEBERG_LEDGER_RUNTIME_USER` to the restricted
   `ledger_reader` role — unrelated to Phase 2 schema, but it closes the
   app-credential tamper path and is the highest-value single config change.

## 6. Rollback

The schema change is additive and nullable, so rolling *back* the code is safe:
old code simply ignores the new columns (its canonical form never referenced
them, and the new columns are `NULL` on any row old code wrote). New rows written
by Phase-2 code that carry the new fields would, however, fail `verify_chain()`
under *old* code — because old code's `verify_chain` doesn't add those fields to
the canonical form. So: rolling back the code is safe **only if** no Phase-2 rows
with populated new fields (or the two new record kinds) have been written yet.
Once Phase-2 rows exist, the Phase-2 verifier must stay deployed. Plan rollback
accordingly — the schema never needs to roll back, but the verifier does.
