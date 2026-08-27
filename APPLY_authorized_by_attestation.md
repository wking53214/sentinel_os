# Apply document — keyed attestation for `authorized_by`

Delivered as **PR #28** (`wking53214/sentinel_os`), branch
`authorized-by-attestation`, three commits:

1. `4ee0065` — the core keyed attestation.
2. `bb70516` — `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` + deploy wiring (open
   question 1).
3. `080c935` — key rotation: verification key set + per-signature fingerprint
   (open question 2).

`authorized_by_attestation.patch` in the repo root is the full diff of all
three against `origin/main`, verified to apply cleanly in a fresh worktree.
Nothing is merged. `main` is untouched.

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
test files (237 tests) passes.

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
  — wiring the keys through.

The scope grew after the initial delivery because the repo owner chose to
answer open questions 1 (key storage) and 2 (rotation) as part of this PR
rather than as follow-ups. Question 2 added no new files — it is contained in
`authorized_by_attestation.py`, a widen-only migration in `ledger_postgres.py`,
and the docs/ops files.

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
- `attestation_keyset()` → `KeySet` — the keys this process accepts for
  verification: the current signing key + `..._KEYS_PREVIOUS` (trusted) +
  `..._KEYS_RETIRED` (recognised but distrusted). Comma-separated env, or one
  key per line via a `_FILE` variant. A set-but-unreadable `_FILE` raises.
- `key_fingerprint(key)` — 16 hex of `sha256(domain‖key)`, the id carried in a
  v2 signature so verification can pick the right key after a rotation.
- `enforcement_required()` — reads `ICEBERG_LEDGER_REQUIRE_ATTESTATION`. Off
  unless set to `1/true/yes/on` (D3).
- `sign_authorized_by(authorized_by, previous_hash, record_kind, key)` —
  returns the **v2 envelope** `abv2.<keyfp>.<digest>` (HMAC-SHA256 over a
  domain-tagged, narrow payload `{"authorized_by", "previous_hash",
  "record_kind"}`), or `None` when there is no claim or no key.
- `verify_authorized_by_signature(row, keys)` — `keys` may be a `KeySet`, a
  single key, an iterable, or `None`. Returns `(status, detail)`, status one of
  `ok / absent / unattested / unverifiable / invalid / retired_key /
  unknown_key`. Comparison is `hmac.compare_digest` (constant time). A v2
  signature names its key, so verification checks exactly that one; a legacy
  bare-digest signature is tried against every held key. `invalid` and
  `unknown_key` mean "something is wrong"; `retired_key` is a deliberate,
  operator-chosen state; `unattested` is the normal state of a pre-key row.

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
  VARCHAR(96)`, nullable, no index, no backfill — same migration guarantee as
  every prior optional column. A ledger created at the earlier `VARCHAR(64)`
  is widened in place (guarded on current length; catalog-only in PG 9.2+, no
  table rewrite); existing 64-char digests are untouched and still verify.
- `__init__` also builds `attestation_keyset()` once at startup so a broken
  key-source *file* (`_KEY_FILE`, `..._KEYS_PREVIOUS_FILE`,
  `..._KEYS_RETIRED_FILE` — set but unreadable) surfaces at construction, not
  on first verify. No-op when nothing is configured. This is a small behaviour
  change from `bb70516`, where a broken primary `_KEY_FILE` with enforcement
  *off* would only have surfaced on the first write; it now raises at
  construction. Correct (fail fast on a misconfigured secret mount) and only
  affects a deployment that set the var. Tested both ways.
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
  untouched); and, **only when this deployment holds at least one attestation
  key**, runs the keyed check. `invalid` and `unknown_key` rows are always
  violations; `retired_key` rows are violations only when enforcement is on. A
  NULL signature (unattested) is never a violation. With no key configured,
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

37 tests: the 8 required properties; key-file resolution / precedence /
broken-file failure modes; cross-record-kind drift; the `VARCHAR(64)→(96)`
in-place widen on upgrade; and key rotation — the v2 envelope and its
fingerprint, the current/previous/retired key set, a clean `verify_chain`
across a rotation, `unknown_key` failing with enforcement off, `retired_key`
failing only with enforcement on, and legacy bare-digest rows still verifying
via try-all-keys. Tampering is simulated as pure-function tests on row dicts
(the
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

**Note on the baseline.** During this work `main` advanced ~11 commits from a
concurrent process (a "conservation gateway" / PERCEIVE integration effort —
all new files, no overlap with anything here). The branch was **rebased onto
the current `origin/main`** (clean, zero conflicts — the only near-touch is
`governance/__init__.py`, which that work adds and this does not modify). The
numbers below are against that rebased base:

| | passed | failed | skipped | errors |
|---|---|---|---|---|
| `origin/main` (7c830db) | 736 | 26 | 22 | 26 |
| this branch on top      | 777 | 22 | 22 | 26 |

`+41` passed / `−4` failed. Breakdown:

- **`+37`** — the new `test_authorized_by_attestation.py` (8 required
  properties + 5 key-file resolution + 2 migration/startup + 14 key rotation +
  8 misc coverage).
- **`+4` / `−4`** — `test_tls_security.py`'s four cert tests, which flip
  pass↔fail between runs depending on whether an earlier test in the run
  generated `certs/`. Pure run-order nondeterminism in the pre-existing
  suite; **not caused by this branch** (they were failing on the
  `origin/main` run and passing on the branch run purely by collection
  order). No test's outcome changed because of a code change here.

**No new failures or errors introduced.** The 22 failures and 26 errors on the
branch are all pre-existing: `ModuleNotFoundError: No module named
'observe_perceive_core'` (a module absent from this checkout, imported by
`production_harness.py`) plus the concurrent work's own conservation-test
failures. Confirmed by diffing the sorted `FAILED`/`ERROR` lists — the only
delta is the flaky TLS quartet above.

Scope note: the baseline is over `Tests/`. A few `test_*.py` sit at the code
root (`test_twin_custody.py`, `test_twin_live.py`,
`test_twin_snapshot_forgery.py`) and were outside it;
`test_twin_custody.py::test_recompute_catches_field_edit` fails on a clean
tree, before any change here.

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

- The row was written by a component that held one of the configured
  attestation keys at write time, and (for a v2 signature) *which* key —
  `abv2.<keyfp>.<digest>`.
- The `authorized_by` string on the row has not been altered since it was
  written — including against an attacker who *also* rebuilds the unkeyed
  SHA-256 chain to keep `current_hash` self-consistent (the acknowledged,
  out-of-scope limitation of the plain chain).

**Does not prove:**

- That the named human or role authorized anything. `authorized_by` remains
  an unverified claim; the only check any write path applies to it is that it
  is non-empty.
- Which holder of a key wrote the row. Keys are service-level, not
  per-identity (D1); every holder of a key — or any process that has read it —
  is indistinguishable.
- Anything at all once a key is compromised. A leaked key forges every
  signature this mechanism would accept under that key. (This is exactly why
  the RETIRED role exists: moving a suspected-compromised key there makes
  every row it signed read as `retired_key`.)

Every docstring, comment, field description, and error message added by this
change states this explicitly. It is described throughout as *attesting that
the record was written by an authorized writer and has not been altered
since* — never as verifying authority, authenticating an identity, or proving
authorization.

**Enforcement (`ICEBERG_LEDGER_REQUIRE_ATTESTATION`) has three teeth:**
(a) the ledger refuses to start without a signing key (`__init__`); (b) the
writer runs a post-signing self-check and refuses a row whose present
`authorized_by` claim it could not sign; (c) `verify_chain` treats a
`retired_key` row as a violation (an `unknown_key` row is always a violation,
enforced or not). There is **no caller-supplied-signature path** — the ledger
always signs its own rows — so no API caller can present an invalid signature
to be refused at write time. Detection of an *altered* claim happens at
verification time (`verify_chain`, or `verify_authorized_by_signature`
directly), not at write time.

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
only as one more optional hashed field. No API signature changed incompatibly
(the new helper is internal; no public parameter was added — `verify_chain`
and `verify_authorized_by_signature` kept their signatures, the latter now
also accepting a `KeySet`). No new dependency. **No pre-existing column was
renamed, retyped, or dropped.** The only column whose type changed is
`authorized_by_sig`, which this PR itself introduces — it started life at
`VARCHAR(64)` in commit `4ee0065` and this commit widens it to `VARCHAR(96)`
for the v2 envelope, a lossless catalog-only change (PG 9.2+). A ledger that
ran the earlier commit is widened in place at next construction; a test
exercises that path (`test_upgrade_widens_authorized_by_sig_from_64_to_96`).

The "no identity or key-management system beyond a single environment-supplied
secret" stop condition is the one to weigh. What was added is deliberately the
minimum that makes rotation safe: two more environment-supplied lists
(`..._KEYS_PREVIOUS`, `..._KEYS_RETIRED`) and a key fingerprint in the
signature. There is no KMS client, no rotation automation, no per-identity
keys, no new service. If the owner reads this as crossing the line, the
rotation half (question 2) can be dropped without affecting the core — but it
was explicitly requested after the core landed.

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
  questions 1 (key storage) and 2 (rotation) as part of this PR rather than
  follow-ups.

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
without a configured key. Closing the chain-hash gap itself was explicitly out
of scope.

**The twin does not verify signatures**, only recomputes hashes
(`deep_verify_row`). It ships the `authorized_by_sig` column so its hash
recompute stays correct, but it holds no attestation key and does not run
`verify_authorized_by_signature`. Giving the customer-DR witness the signing
key is a custody question outside this change; the primary's `verify_chain` is
where the keyed check runs.

**v1 legacy rows report `invalid`, not `unknown_key`, when their signing key
is dropped entirely.** A v1 bare-digest signature carries no key id, so
verification can only try every held key and return `invalid` on total
failure — it reads as tampering even when the real cause is "we no longer
hold that key." v2 rows do not have this problem (they name their key).
Impact is minimal: v1 rows exist only where the intermediate commit ran with
a key configured, i.e. essentially nobody for an unmerged PR. The mitigation
is a policy one — move a key that ever signed v1 rows to `RETIRED` rather than
dropping it — and it is documented in the module docstring.

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

2. **Key rotation — design now or defer? — ANSWERED, built in.** The owner
   chose to design it now. Verification holds a **key set**, not one key:
   the current signing key, `ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS` (still
   trusted → `ok`), and `ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED` (deliberately
   distrusted → `retired_key`). New signatures carry a 16-hex fingerprint of
   the signing key (`authorized_by_sig` becomes `abv2.<keyfp>.<digest>`;
   `authorized_by_sig` widened `VARCHAR(64)`→`VARCHAR(96)`, catalog-only),
   so verification matches each row to the exact key that signed it and a
   rotation never makes honest history read as forged. Legacy bare-digest
   rows fall back to trying every held key.

   `verify_chain` treatment, per the owner's decision:
   - `unknown_key` (signed by a key held in **no** role) → **always** a
     violation, in every configuration, un-overridable.
   - `retired_key` (signed by a deliberately distrusted key) → a violation
     **only under enforcement**.

   Full rotation runbook + the dashboard query (`split_part(authorized_by_sig,
   '.', 2)`) is in `DEPLOYMENT.md`. `..._PREVIOUS` is wired through
   docker-compose and k8s (optional). No per-row re-signing (append-only +
   immutability triggers).

3. **GSA-815** has its own independent governance kernel. Should it receive
   the same mechanism, or is divergence acceptable here as it is elsewhere
   between the two platforms? No change was made to GSA-815.
