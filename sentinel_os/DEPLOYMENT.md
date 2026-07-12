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
  kubectl apply -f k8s/
  ```
- `Deploy/k8s/` + `Deploy/argocd/` — a fuller split (`iceberg-api.yaml`,
  `iceberg-rl.yaml`, `iceberg-sim-workers.yaml`, `hpa.yaml`, `ingress.yaml`)
  plus an ArgoCD `application.yaml` for GitOps-managed rollout.

Pick one path per environment — applying both against the same cluster will
create duplicate/conflicting resources. Neither path has been verified
end-to-end from this repo; treat both as a starting point, not a tested
runbook, until a real cluster deploy confirms them.

## Database

The ledger expects a reachable PostgreSQL instance matching the `POSTGRES_*`
variables above. CI provisions one via a `postgres:16` service container
(see `.github/workflows/tests.yml`) purely for test purposes — it is not a
production database setup.

## Known gaps (see README "Tests" section for current numbers)

- Live Claude API round-trip (governed decision path with a real key) is not
  yet verified end-to-end outside of unit-level mocking.
- The two Kubernetes manifest sets above have not been deploy-tested from
  this repo.
