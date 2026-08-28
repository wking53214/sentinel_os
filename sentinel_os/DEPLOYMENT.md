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
   > `unknown_key`. That is not fatal — `verify_ledger` / the `/verify`
   > endpoint run in tolerant mode and *report* violations rather than
   > crashing — but it lights up the entire back-catalogue until A is
   > restored to the list. During a rolling deploy, instances that have B as
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
IVR lane. This kernel has no k8s manifests of its own yet — the governed lane
is currently `docker-compose.yml` only.

- `Deploy/k8s/` + `Deploy/argocd/` — **flagged, not verified current, likely dead.**
  All 5 files in this tree are internally consistent with each other but
  not with the current codebase: `iceberg-api.yaml` targets a different
  image (`iceberg-runtime:3.x` vs `iceberg:latest`) and port (8000 vs
  9090); `iceberg-rl.yaml` and `iceberg-sim-workers.yaml` deploy RL/sim
  workers for engines (`Engines/rl_ppo.py`, `Engines/rl_marl.py`) that no
  longer exist in this repo; `hpa.yaml` scales the old `iceberg-api`
  deployment name. `Deploy/argocd/application.yaml` isn't even a valid
  ArgoCD `Application` resource — it contains the same
  `server`/`governance`/`rl` config block as the old ConfigMap, just
  under the wrong folder, which suggests this tree was generated/copied
  incorrectly rather than actively maintained. Don't apply any of it
  without confirming what it's actually meant to target.

## Database

The ledger expects a reachable PostgreSQL instance matching the `POSTGRES_*`
variables above. CI provisions one via a `postgres:16` service container
(see `.github/workflows/tests.yml`) purely for test purposes — it is not a
production database setup.

For a non-local Postgres, set a real `POSTGRES_PASSWORD` in your environment
before bringing up `docker-compose.yml`.

## Known gaps (see README "Tests" section for current numbers)

- Live Claude API round-trip (governed decision path with a real key) is not
  yet verified end-to-end outside of unit-level mocking.
- The two Kubernetes manifest sets above have not been deploy-tested from
  this repo.
