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

- `Deploy/k8s/` + `Deploy/argocd/` — **flagged, not verified current.**
  This set describes a different image (`iceberg-runtime:3.x` vs
  `iceberg:latest`), a different port (8000 vs 9090), and a
  `CONFIG_PATH`-driven YAML config referencing `rl.ppo` / `rl.marl` and a
  `build_graph` routing system. Those RL engines
  (`Engines/rl_ppo.py`, `Engines/rl_marl.py`) no longer exist in this
  repo. This looks like leftover infrastructure from an earlier
  architecture generation rather than something that matches the current
  cassette-governed system — don't apply it as-is without confirming
  which architecture it's meant to target.

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
