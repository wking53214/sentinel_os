# Deployment

## Required environment variables

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Ledger database host |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `iceberg` | |
| `POSTGRES_USER` | `iceberg` | |
| `POSTGRES_PASSWORD` | `iceberg` | Change for any non-local deployment |
| `CLAUDE_API_KEY` | — | Used by the IVR governor client, which now lives in the **GSA-815** repo (`claude_governance_api.py`). Not read by this kernel directly. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_API_KEY` / `TWILIO_API_SECRET` | — | Twilio call-log ingestion moved to the **GSA-815** repo. |
| `ICEBERG_API_KEYS` | — | API keys for the resilient API server (`api_server_resilient.py`), now in the **GSA-815** repo. |
| `ICEBERG_LEDGER_ATTESTATION_KEY` | — | Current signing key for the ledger `authorized_by` attestation. Unset → rows written unattested (default). See below. |
| `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` | — | Path to a file holding the current key; used only when `ICEBERG_LEDGER_ATTESTATION_KEY` is unset. For file-projecting secret managers (Vault Agent, CSI driver, Docker secrets). |
| `ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS` / `..._PREVIOUS_FILE` | — | Keys retired from signing but still fully trusted for verification (comma-separated, or one per line in the file). Where old keys live after a rotation. |
| `ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED` / `..._RETIRED_FILE` | — | Keys the operator has deliberately stopped trusting (suspected compromise / policy sunset). Rows they signed verify as `retired_key` — a `verify_chain` violation only under enforcement. |
| `ICEBERG_LEDGER_REQUIRE_ATTESTATION` | `false` | When truthy, an `authorized_by` claim without a valid signature is refused, the ledger refuses to start if no signing key is configured, and a `retired_key` row is a `verify_chain` violation. |
| `PORT` | `9090` | API server port |
| `CERT_FILE` / `KEY_FILE` | `./certs/cert.pem` / `./certs/key.pem` | TLS cert/key paths |

## TLS certificates

`certs/` is gitignored on purpose — private keys should never be committed.
Generate your own before running the TLS-dependent tests or serving HTTPS:

```
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem \
  -days 365 -subj "/CN=your-domain"
```

(CI generates an ephemeral cert automatically — see `.github/workflows/tests.yml`.)

**Note on git history:** a self-signed dev cert/key pair (`certs/cert.pem`,
`certs/key.pem`, generic `CN=localhost` subject) was committed in an early
commit (`8dfa1c6e`) before `certs/` was gitignored. It's still retrievable
from git history. Decision: leave history as-is rather than rewrite it —
the cert is a throwaway self-signed placeholder, never used for any real
deployment, and rewriting history would break every existing clone. If you
ever *did* use that specific keypair for something real, treat it as
compromised and regenerate; otherwise no action needed.

## Ledger `authorized_by` attestation

The ledger's `authorized_by` field records who a decision or ledger action is
attributed to. Without a key it is an unverified string. Configure
`ICEBERG_LEDGER_ATTESTATION_KEY` and every writer signs the claim with
HMAC-SHA256; a verifier with the same key then confirms the row was written by
a key holder and that the claim is unchanged. It does **not** prove the named
party authorized anything — one shared key, and a leaked key forges
signatures. See `governance/authorized_by_attestation.py`.

- **Generate:** `openssl rand -hex 32`. One system of record, never committed,
  a **distinct key per environment** (a leaked staging key must not forge
  production rows).
- **Deliver it** either as `ICEBERG_LEDGER_ATTESTATION_KEY` directly, or — for
  Vault Agent / the Secrets Store CSI driver / Docker secrets, which project a
  secret as a file — mount the file and set
  `ICEBERG_LEDGER_ATTESTATION_KEY_FILE` to its path (env var wins if both are
  set; a set-but-unreadable file path is a hard error, never silently "no
  key"). The key is re-read on every write/verify, so rewriting the file
  rotates it with no restart.
- **Enforcement:** set `ICEBERG_LEDGER_REQUIRE_ATTESTATION=1` to make an
  unsigned `authorized_by` claim a hard failure. With enforcement on and no
  signing key configured, the ledger refuses to start. Leave it off (default)
  to roll the key out first and sign opportunistically.

### Key rotation

Every v2 signature carries a 16-hex fingerprint of the key that made it
(`authorized_by_sig` looks like `abv2.<keyfp>.<digest>`). Verification holds a
set of keys and matches each row to the one that signed it, so a rotation does
not make old rows read as forged.

Three roles, each with an env var and a `_FILE` variant:

| role | var | a matching row verifies as | `verify_chain` |
|---|---|---|---|
| **current** — signs new rows | `ICEBERG_LEDGER_ATTESTATION_KEY` | `ok` | — |
| **previous** — retired from signing, still trusted | `ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS` | `ok` | — |
| **retired** — deliberately distrusted, still recognised | `ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED` | `retired_key` | violation **only under enforcement** |
| *(none of the above)* | — | `unknown_key` | violation **always, un-overridable** |

**Runbook:**

1. Generate key B (`openssl rand -hex 32`), add it to the secret store.
2. Append the current key A to `ICEBERG_LEDGER_ATTESTATION_KEYS_PREVIOUS`
   (keep anything already there).
3. Set `ICEBERG_LEDGER_ATTESTATION_KEY` = B. Roll the fleet.

   > **Do step 2 before step 3, and let it propagate.** If B becomes the
   > signing key while A is not yet in `..._KEYS_PREVIOUS` anywhere,
   > verification stops recognising A: every pre-rotation row reports
   > `unknown_key`. That is not fatal — `scripts/verify_ledger.py` runs in
   > tolerant mode and *reports* violations rather than crashing — but it
   > lights up the entire back-catalogue until A is restored to the list. During a rolling deploy, instances that have B as
   > current and A in `..._KEYS_PREVIOUS` verify cleanly; the danger window is
   > only if step 3 lands before step 2.

4. Confirm every writer has moved:
   ```sql
   SELECT split_part(authorized_by_sig,'.',2) AS keyfp, count(*), max(timestamp)
   FROM ledger_entries
   WHERE authorized_by_sig LIKE 'abv2.%'
   GROUP BY 1;
   ```
   Once no rows with A's fingerprint appear after the cutover instant, the
   rotation is complete.
5. **Leave A in `..._PREVIOUS` indefinitely.** Keys are tiny; retaining them
   keeps all of history verifiable. Only if A is believed *compromised*, move
   it to `ICEBERG_LEDGER_ATTESTATION_KEYS_RETIRED` — its rows then light up as
   `retired_key`, flagging exactly the history a leaked key could have forged.

**Retiring a key entirely** (removing it from both lists) makes the rows it
signed `unknown_key`, which `verify_chain` fails unconditionally. That is a
deliberate governance decision to stop being able to verify that slice of
history — a conservative deployment never does it.

There is no per-row re-signing: the ledger is append-only and the immutability
triggers forbid rewriting a row.

## Local / single-machine

```
docker-compose up -d
```

`docker-compose.yml` is the governed-lane stack for this kernel: `ledger`
(Postgres) + `redis` + `ingress` (`api_server_v2.py`) + `worker`
(`sentinel_worker.py` → `GovernanceHarness`).

The IVR / Iceberg application — the standalone simulator, the resilient API
server (`api_server_resilient.py`), `docker-compose-prod.yml`, and the `k8s/`
manifests — moved to the **GSA-815** repo. Deploy that lane from there; it
runs on this kernel.

## Kubernetes

The `k8s/` manifests (a single `iceberg` Deployment running
`api_server_resilient.py`) moved to the **GSA-815** repo with the rest of the
IVR lane. This kernel has no k8s manifests of its own — the governed lane is
`docker-compose.yml` only.

A `Deploy/k8s/` + `Deploy/argocd/` tree was removed 2026-09-03: five files that
were internally consistent with each other but not with any current code
(wrong image and port, deployed RL/sim workers for `Engines/` modules that no
longer exist, an `argocd/application.yaml` that wasn't a valid `Application`
resource). If you need a k8s target for the kernel, write it against the
current `docker-compose.yml` services rather than resurrecting that tree from
git history.

## Database

The ledger expects a reachable PostgreSQL instance matching the `POSTGRES_*`
variables above. CI installs PostgreSQL natively on the runner (so
`test_twin_live.py` can use Unix-socket peer auth — see
`.github/workflows/tests.yml`); that is a test setup, not a production one.

For a non-local Postgres, set a real `POSTGRES_PASSWORD` in your environment
before bringing up `docker-compose.yml`. The app connects as a restricted
runtime role (`ICEBERG_LEDGER_RUNTIME_USER`, see above) — the ledger refuses to
start if that role is a superuser or the table owner.

### Backups

The ledger is the record. Back the database up on the schedule your retention
obligations require, encrypted at rest, with the backup-encryption key held
separately from whoever administers the database (an auditor will ask — see
`AUDIT_PLAYBOOK.md` section 5).

A backup you have not restored is not a backup, and for a hash-chained ledger
"it restored" is not enough — the chain has to still verify. Run the drill:

```
scripts/ledger_backup_verify.sh  [OUTFILE]
```

It `pg_dump`s the ledger, restores into a throwaway database, runs
`scripts/verify_ledger.py` (a thin CLI over `PostgreSQLLedger.verify_chain()`)
against the restored copy, and drops the throwaway database. Exit 0 means the
dump restores and its chain verifies clean. Wire it into the job that ships
backups offsite, so a bad backup fails loudly instead of sitting unnoticed
until you need it.

`scripts/verify_ledger.py` also runs standalone against any database
(`--db NAME`, or `POSTGRES_*` env) — the same check an auditor runs.

### Ledger integrity incidents

If `verify_chain()` / `verify_ledger.py` reports a violation:

1. **Do not write to the ledger.** Snapshot the database immediately (a plain
   `pg_dump`) so the current state is preserved for investigation.
2. **Read the violation.** `Entry N: content hash mismatch (stored=…,
   recomputed=…)` means row N's stored `current_hash` no longer matches a
   recomputation of its contents — an in-place edit, corruption, or a restore
   from a tampered source. `Entry N: chain broken (prev_hash mismatch)` means
   the chain link at N is broken — a row was inserted, deleted, or reordered.
   `Entry N: authorized_by …` means the keyed attestation on that row's
   attribution string doesn't check out (see "Ledger `authorized_by`
   attestation" above).
3. **Distinguish accident from action.** A single mismatch with a plausible
   cause (disk error, a botched migration) is likely corruption — restore from
   the last backup whose `ledger_backup_verify.sh` run passed. A *clean* full
   chain that simply disagrees with an external record is the harder case:
   `AUDIT_PLAYBOOK.md` (H3/H5) shows an operator with database superuser access
   can disable the immutability triggers and produce a fully self-consistent
   rewrite. `verify_chain()` cannot tell you that happened.
4. **Cross-check the twin.** If a twin replica is configured, compare its
   independently-held chain head and per-decision receipts against the primary.
   Divergence there is the signal `verify_chain()` alone cannot give you. This
   is the whole reason the twin exists.
5. **Restoring does not clear the finding.** A clean `verify_chain()` after a
   restore proves the backup is internally consistent, not that it was never
   tampered before the dump. Treat a confirmed rewrite as a reportable incident,
   not a footnote.

### Growth

The ledger is append-only and never shrinks — one row per governed decision,
plus one `cassette_binding` row per distinct cassette version. There is no
automatic partitioning or archival of old entries yet; for high-volume
deployments, plan table partitioning by time or `id` range before the table
gets large enough that `verify_chain()` (a full-table recomputation) becomes
slow.

## Known gaps (see README "Tests" section for current numbers)

- Live Claude API round-trip (governed decision path with a real key) is not
  yet verified end-to-end outside of unit-level mocking.
- The two Kubernetes manifest sets above have not been deploy-tested from
  this repo.
