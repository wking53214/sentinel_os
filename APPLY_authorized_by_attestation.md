# Apply document — keyed attestation for `authorized_by`

Deliverable: `authorized_by_attestation.patch` (unified diff, `git apply` from the
repository root — the directory that contains `sentinel_os/`). Nothing has been
committed, branched, or pushed.

Patch verified to apply cleanly against the current `main`
(`git apply --check` in a fresh worktree at HEAD).

---

## 0. Note on the two twin-recompute tests (previously flagged as a §7 item)

An earlier draft of this document flagged a stop-and-report condition: with an
attestation key configured, two existing tests
(`test_contract_attestation.py::test_twin_recomputes_every_contract_kind_identically`
and `test_human_selection_ledger.py::test_twin_recomputes_human_selection_identically`)
failed, because they built the twin's input `row` dict by hand-selecting a
column subset instead of using `twin_custody.SHIPPED_COLUMNS`, so they dropped
the new `authorized_by_sig` column.

**Resolved at the repository owner's explicit direction:** both tests now
`SELECT` the `SHIPPED_COLUMNS` list and build the row with
`dict(zip(SHIPPED_COLUMNS, r))` — the same pattern
`test_regulatory_cassettes.py` and `test_phase2_limitations.py` already use.
The assertions and intent are unchanged; they no longer silently drop whichever
optional hashed field was added most recently.

After this, **no existing test fails in any configuration** — default (no key)
or with `ICEBERG_LEDGER_ATTESTATION_KEY` set. A with-key sweep of 11 ledger
test files (229 tests) passes.

---

## 1. What changed and in which files

Thirteen files:

- **Ledger hash contract (4):** `governance/authorized_by_attestation.py`
  (new), `governance/ledger_postgres.py`, `canonical_fields.py`,
  `twin_custody.py`.
- **Docs-only (1):** `regulatory_checks.py`.
- **Tests (3):** `Tests/test_authorized_by_attestation.py` (new);
  `Tests/test_contract_attestation.py` and
  `Tests/test_human_selection_ledger.py` brought onto `SHIPPED_COLUMNS`.
- **Deployment/ops (5):** `DEPLOYMENT.md`, `docker-compose.yml`,
  `docker-compose-prod.yml`, `k8s/deployment.yaml`, `k8s/secret.yaml.example`
  — wiring the key through, added after the repo owner chose to answer open
  question 1 (key storage) now rather than defer it.

### `sentinel_os/governance/authorized_by_attestation.py` — NEW (in the ledger module)

The mechanism, standard library only (`hmac`, `hashlib`, `json`, `os`):

- `attestation_key()` — resolves the key: `ICEBERG_LEDGER_ATTESTATION_KEY`
  (verbatim) if set; else the contents of the file at
  `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` (whitespace stripped) if that is set;
  else `None`. No default, no placeholder fallback (D4). A `..._KEY_FILE` path
  that is set but unreadable or empty **raises** — a named-but-broken key
  source is a misconfiguration, not "no key", and must not silently degrade to
  unattested writes. The file form is the idiom for secret managers that
  project a secret as a file (Vault Agent, Secrets Store CSI driver, Docker
  secrets); the key is re-read every call, so rewriting the file rotates it
  with no restart.
- `enforcement_required()` — reads `ICEBERG_LEDGER_REQUIRE_ATTESTATION`. Off
  unless set to `1/true/yes/on` (D3).
- `sign_authorized_by(authorized_by, previous_hash, record_kind, key)` —
  returns hex HMAC-SHA256 over a domain-tagged, narrow payload
  (`{"authorized_by", "previous_hash", "record_kind"}`), or `None` when there
  is no claim or no key.
- `verify_authorized_by_signature(row, key)` — returns `(status, detail)` where
  status is one of `ok / absent / unattested / unverifiable / invalid`.
  Comparison is `hmac.compare_digest` (constant time). Only `invalid` means
  "something is wrong with this row"; `unattested` is the normal, honest state
  of any row written without a key and every row that predates the column.

The payload is deliberately three values that are each already a dedicated
column at all three hash-recompute sites, so recomputing the signature needs
no per-record-kind reconstruction and adds no new byte-exactness drift
surface. `previous_hash` transitively commits every earlier row and, because
`ledger_entries.current_hash` is UNIQUE, pins the signature to one chain
position for every row after genesis, so a signature cannot be replayed onto a
different row. (The genesis row's `previous_hash` is the literal `"genesis"`,
so a signature on row 1 of a table is not position-bound — immaterial in
practice: writing row 1 of a rebuilt table is already total compromise and the
immutability triggers block a rebuild in place.)

### `sentinel_os/canonical_fields.py` — the shared optional-field contract

`"authorized_by_sig"` appended to `OPTIONAL_HASHED_FIELDS`. This is the single
list all three recompute sites iterate; an absent/NULL value is omitted from
the canonical form, exactly as for every optional field before it. The
`authorized_by` comment is corrected to say the field is a claim, not a
verified fact.

### `sentinel_os/twin_custody.py` — the witness

`"authorized_by_sig"` appended to `SHIPPED_COLUMNS` so the twin ships the
column and its `recompute_current_hash` (which already applies the shared
contract to the whole row) folds it into the hash automatically.

### `sentinel_os/governance/ledger_postgres.py` — the writer + the primary verifier

- Import of the new module.
- `__init__`: if enforcement is required and no key is configured, raise
  `RuntimeError` before the connection pool is built — a signing system with a
  publicly known (or absent) key must not appear to protect anything (D4).
- `_initialize_schema`: `ADD COLUMN IF NOT EXISTS authorized_by_sig
  VARCHAR(64)`, nullable, no index, no backfill — same migration guarantee as
  every prior optional column.
- New helper `_authorized_by_sig(authorized_by, previous_hash, record_kind)`:
  computes the signature; when enforcement is on and a present claim could not
  be signed, refuses the write. Note this self-check is **not reachable
  through the API**: the ledger signs its own rows, so with a key configured
  the signer always returns a value and this guard can only fire if signing
  itself throws or is stubbed out (test 6 stubs it). Enforcement-on therefore
  has exactly two teeth — see §4.
- Every one of the ten writer paths that populates `authorized_by`
  (`append_decision`, `bind_cassette_version`,
  `record_regulatory_cassette_event`, `record_regulatory_disclosure`,
  `record_recommendation_shadow_run`, `record_recommendation_shadow_score`,
  `record_human_selection`, `record_outcome_harm_event`,
  `_append_contract_row`, `supersede_decision`) now computes the signature,
  adds it to its optional-field source dict, and writes the new column.
- `verify_chain`: selects the new column; folds it into every record kind's
  canonical form at the single recompute point (one shared
  `apply_optional_hashed_fields` call, the same way `twin_custody` applies the
  contract to the whole row — the per-kind literal dicts above are left
  untouched); and, **only when a key is configured**, runs the keyed
  attestation check and reports `invalid` rows as violations. With no key,
  `verify_chain` behaves exactly as before.

### `sentinel_os/regulatory_checks.py` — docs only

`TierDeclaration.authorized_by` and the confidence-scale comment reworded to
state plainly that naming a party does not establish that the party holds any
authority or reviewed anything. No code path changed.

### `sentinel_os/Tests/test_contract_attestation.py`, `sentinel_os/Tests/test_human_selection_ledger.py` — existing tests brought onto `SHIPPED_COLUMNS`

Each has one twin-recompute test that built its `row` dict from a hand-picked
column list. Both now `SELECT` `twin_custody.SHIPPED_COLUMNS` and build the
row with `dict(zip(SHIPPED_COLUMNS, r))`. Assertions unchanged. See §0. Done
at the repository owner's explicit request (this is the one deviation from
"no existing test modified").

### `sentinel_os/Tests/test_authorized_by_attestation.py` — NEW

21 tests (the 8 required properties, plus key-file resolution, precedence,
broken-file failure modes, and cross-record-kind drift). Tampering is
simulated as pure-function tests on row dicts (the
`ledger_entries` table carries an `UPDATE`-blocking immutability trigger, so
an in-place edit of a live row is not possible — this mirrors how
`twin_custody.deep_verify_row` is already tested).

### Deployment / ops — key wiring (config + docs, no logic)

- `sentinel_os/DEPLOYMENT.md` — the three env vars added to the table, plus a
  new "Ledger `authorized_by` attestation" section (generate with
  `openssl rand -hex 32`, env var vs `_KEY_FILE`, enforcement, the rotation
  caveat).
- `sentinel_os/docker-compose.yml` — `ICEBERG_LEDGER_ATTESTATION_KEY:
  ${ICEBERG_LEDGER_ATTESTATION_KEY:-}` on the `iceberg` and `worker` services
  (both construct the ledger), taken from the operator's environment, no
  committed value.
- `sentinel_os/docker-compose-prod.yml` — same, on its `iceberg` service.
- `sentinel_os/k8s/deployment.yaml` — an **optional** `secretKeyRef` for
  `ICEBERG_LEDGER_ATTESTATION_KEY` (key `ledger-attestation-key`), plus a
  commented `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` block showing the
  file-mount path for CSI/Vault.
- `sentinel_os/k8s/secret.yaml.example` — `ledger-attestation-key: ""` added
  with a note, and `openssl rand -hex 32` in the `kubectl create secret`
  example.

All five default to "unset → unattested", so applying them changes nothing
until an operator supplies a key.

---

## 2. Before / after test counts

Full suite: `python3 -m pytest Tests/ --continue-on-collection-errors`
(Postgres is available in this environment, so the ledger tests run for real).

| | passed | failed | skipped | errors |
|---|---|---|---|---|
| before | 746 | 16 | 22 | 26 |
| after  | 767 | 16 | 22 | 26 |

`+21` passed = the new test file (16 for the required properties + 5 for
key-file resolution). The failed / skipped / errored sets are
**byte-identical** before and after (`diff` of the sorted `FAILED`/`ERROR`
lines is empty). The two twin-recompute tests brought onto `SHIPPED_COLUMNS`
(§0) were already passing at the default configuration before this change and
still pass; they now also pass with an attestation key set, which they did not
before. A with-key sweep of 11 ledger test files (229 tests) passes.

All 16 pre-existing failures and all 26 errors share one pre-existing,
unrelated root cause: `ModuleNotFoundError: No module named
'observe_perceive_core'` (a module absent from this checkout; imported by
`production_harness.py`). This is an environment gap, not caused by this work,
and is present identically before and after. Note it is a *different* gap from
the three the task named (Postgres / Redis / `runuser`) — in this environment
Postgres and `redis-server` are both present and `runuser` is absent; the
counts above already account for that.

Scope note: the baseline was taken over `Tests/`. A handful of `test_*.py`
files sit at the code root rather than under `Tests/` (`test_twin_custody.py`,
`test_twin_live.py`, `test_twin_snapshot_forgery.py`); they were outside the
baseline. `test_twin_custody.py::test_recompute_catches_field_edit` fails on
the clean tree, before any change here (verified by stashing).

---

## 3. Which of the eight required tests were proven to fail pre-change

Proven by running against the clean tree (implementation stashed, new module
moved aside):

- **Test 3** (altering the signature breaks chain verification — proves D5).
  Bites as a logic failure, demonstrated without importing the new module: a
  standalone probe using only `twin_custody` + `canonical_fields` shows that
  pre-change, swapping `authorized_by_sig` on a row dict does **not** change
  `recompute_current_hash` (the key is not in `OPTIONAL_HASHED_FIELDS`), so
  `deep_verify_row` still returns OK and the test's `assert not ok` fails.
  Post-change it breaks the hash. Same probe: `test3_bites=False` pre,
  `test3_bites=True` post.

- **Test 6** (enforcement on → an unsignable `authorized_by` claim is
  refused). Pre-change there is no `_authorized_by_sig` guard, so
  `append_decision` with a stubbed-to-`None` signer *succeeds*, and the test's
  `pytest.raises(RuntimeError)` fails with "DID NOT RAISE". Post-change the
  guard raises.

- **Test 2** (altering `authorized_by` after the fact breaks verification,
  *even after the unkeyed chain is recomputed to stay self-consistent*).
  Pre-change there is no keyed verifier at all — the test module fails to
  import (`cannot import name 'authorized_by_attestation'`). The underlying
  gap it targets is real and independently shown pre-change: after an
  `authorized_by` swap plus a `current_hash` recompute, `deep_verify_row`
  accepts the row (`test2_gap_present=True` both before and after). That is
  precisely what the keyed check closes: post-change,
  `verify_authorized_by_signature` returns `invalid` for that same row.

Tests 1, 4, 5, 7, 8 assert new behaviour (a column, a startup guard, a
verifier) that does not exist pre-change; they were not run against the clean
tree because there is nothing there for them to exercise.

---

## 4. What this mechanism proves, and what it does not

**Proves:**

- The row was written by a component that held
  `ICEBERG_LEDGER_ATTESTATION_KEY` at write time.
- The `authorized_by` string on the row has not been altered since it was
  written — including against an attacker who *also* rebuilds the unkeyed
  SHA-256 chain to keep `current_hash` self-consistent (the acknowledged,
  out-of-scope limitation of the plain chain).

**Does not prove:**

- That the named human or role authorized anything. `authorized_by` remains
  an unverified claim; the only check any write path applies to it is that it
  is non-empty.
- Which holder of the key wrote the row. There is one shared service key (D1);
  two legitimate key holders — or any process that has read the key — are
  indistinguishable.
- Anything at all once the key is compromised. A leaked key forges every
  signature this mechanism would accept.

Every docstring, comment, field description, and error message added by this
change states this explicitly. It is described throughout as *attesting that
the record was written by an authorized writer and has not been altered
since* — never as verifying authority, authenticating an identity, or proving
authorization.

**Enforcement (`ICEBERG_LEDGER_REQUIRE_ATTESTATION`) has exactly two teeth:**
(a) the ledger refuses to start without a key (`__init__`); (b) the writer
runs a post-signing self-check and refuses a row whose present `authorized_by`
claim it could not sign. There is **no caller-supplied-signature path** — the
ledger always signs its own rows — so no API caller can present an invalid
signature to be refused at write time. Detection of an *altered* claim happens
at verification time (`verify_chain` with a key set, or
`verify_authorized_by_signature` directly), not at write time.

**Default behaviour is unchanged.** With no key configured (the default and
the section-6 baseline): writers store `NULL` in the new column, nothing is
refused, `verify_chain` runs exactly as before, and every row hashes
byte-identically to what it hashed pre-change (an absent optional field is
omitted from the canonical form). The only observable difference at the
default configuration is that rows may now carry a signature value — and at
the default they do not.

**On the original stop conditions.** The chain hash is not keyed and
`current_hash` is still `sha256(json.dumps(canonical_entry, sort_keys=True,
default=str))` over the same canonical form; the signature enters that form
only as one more optional hashed field. No identity or key-management system
was introduced beyond the single environment-supplied secret. No column was
renamed, retyped, or dropped. No API signature changed incompatibly (the new
helper is internal; no public parameter was added). No new dependency.

The file count now exceeds the task's "roughly three files outside the ledger
module" guidance, but by the repo owner's direction after the initial
delivery, not silently:

- **Logic outside the ledger module: still three** — `canonical_fields.py`,
  `twin_custody.py` (the hash contract shared with the witness), and
  `regulatory_checks.py` (docs only).
- **Two existing tests** (`test_contract_attestation.py`,
  `test_human_selection_ledger.py`) narrowed to select `SHIPPED_COLUMNS` — see
  §0.
- **Five deployment/ops files** (`DEPLOYMENT.md`, `docker-compose.yml`,
  `docker-compose-prod.yml`, `k8s/deployment.yaml`, `k8s/secret.yaml.example`)
  — config and docs, no logic, added when the owner chose to answer open
  question 1 (key storage) as part of this PR rather than a follow-up.

---

## 5. Found and deliberately not fixed

**Two existing tests hand-rolled the twin's row dict** instead of using
`SHIPPED_COLUMNS` — found because a key-configured run made them fail. Now
fixed (see §0), at the owner's direction. Worth noting for the owner: this is
a recurring latent class — any future optional hashed field will silently be
dropped by any test that hand-picks columns. `test_regulatory_cassettes.py`
and `test_phase2_limitations.py` were already on `SHIPPED_COLUMNS`; after this
change the only remaining hand-rolled twin-recompute row dicts are the
deliberate negative-case builders (`test_a_tampered_contract_row_is_caught...`,
which only checks that the hash *differs*).

**The unkeyed chain hash** remains unkeyed, as instructed. An attacker with
direct write access to `ledger_entries` can still rebuild a self-consistent
chain — but can no longer forge the `authorized_by` signature on any row
without the key. Closing the chain-hash gap itself was explicitly out of
scope.

**`bind_cassette_version` and the regulatory/contract/supersession paths** now
sign too, not just `append_decision`. This is slightly broader than the
finding's single example (the regulatory-lens insertion path) but it is the
same field and the same gap on every record kind that carries it; signing
only some would leave the gap open elsewhere. If the owner wants the scope
narrowed, the per-writer `_authorized_by_sig(...)` calls can be removed
individually without affecting the chain (a writer that does not sign simply
leaves `NULL`, and the row is honestly unattested).

---

## 6. Open questions for the repository owner

1. **Where should the signing key live in deployment? — ANSWERED, built in.**
   The owner chose to settle this now. `attestation_key()` takes the key from
   `ICEBERG_LEDGER_ATTESTATION_KEY` directly, or from a file named by
   `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` (the idiom for Vault Agent / CSI
   driver / Docker secrets — anything that projects a secret as a file). The
   ledger never talks to a secret manager itself; both delivery paths keep it
   ignorant of the source. Wired through `docker-compose.yml`,
   `docker-compose-prod.yml`, `k8s/deployment.yaml`, `k8s/secret.yaml.example`
   (all optional / commented), and documented in `DEPLOYMENT.md`. Key
   generation guidance (`openssl rand -hex 32`, one system of record, distinct
   key per environment) is in the module docstring and `DEPLOYMENT.md`.

2. **Key rotation — design now or defer?** Still deferred (the owner may pick
   this up next). Not designed here. There is no per-row key identifier, so
   after a rotation every pre-rotation signature becomes `invalid` (not
   `unattested`) under the new key. Options: store a key id per row; accept a
   set of keys during a rotation window; or treat rotation as a hard cutover
   and re-verify only forward. This needs a decision before enforcement is
   turned on anywhere with a key that will ever rotate. `DEPLOYMENT.md`
   carries the caveat.

3. **GSA-815** has its own independent governance kernel. Should it receive
   the same mechanism, or is divergence acceptable here as it is elsewhere
   between the two platforms? No change was made to GSA-815.
