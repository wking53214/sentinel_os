# Deployment

## Required environment variables

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | Ledger database host |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `iceberg` | |
| `POSTGRES_USER` | `iceberg` | |
| `POSTGRES_PASSWORD` | `iceberg` | Change for any non-local deployment |
| `CLAUDE_API_KEY` | — | Required for live governance decisions; without it the governor runs in fail-closed no-client mode (see `claude_governance_api.py`) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_API_KEY` / `TWILIO_API_SECRET` | — | Only needed if ingesting real Twilio call logs |
| `ICEBERG_API_KEYS` | — | Comma-separated API keys for the resilient API server (`api_server_resilient.py`) |
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

## Local / single-machine

```
python3 iceberg_complete_simulator.py
docker-compose up -d
```

`docker-compose.yml` is the dev/local stack; `docker-compose-prod.yml` is the
production variant — diff them before assuming parity if you're customizing one.

## Kubernetes

Two manifest sets exist in this repo and are **not** the same deployment path:

- `k8s/` — `deployment.yaml`, `service.yaml`, `pvc.yaml`. Minimal, direct-apply set:
  ```
  kubectl create secret generic iceberg-secrets --namespace=iceberg \
    --from-literal=postgres-host=<your-postgres-host> \
    --from-literal=postgres-password=<real-password> \
    --from-literal=claude-api-key=<real-key>
  kubectl apply -f k8s/
  ```
  This set does not include a Postgres deployment of its own — point
  `postgres-host` at an in-cluster Service or an external managed
  instance you provision separately. See `k8s/secret.yaml.example` for
  the secret's shape (never commit a filled-in version of it).

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

`docker-compose-prod.yml` reads `POSTGRES_PASSWORD` from the environment
(falls back to a placeholder if unset) rather than hardcoding it — set a
real value in your `.env` before deploying anywhere non-local.

## Known gaps (see README "Tests" section for current numbers)

- Live Claude API round-trip (governed decision path with a real key) is not
  yet verified end-to-end outside of unit-level mocking.
- The two Kubernetes manifest sets above have not been deploy-tested from
  this repo.
