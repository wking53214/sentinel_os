# sentinel_os/ — the kernel package

The canonical project README is at the [repository root](../README.md).
This file is a short orientation for the package directory itself.

`sentinel_os/` is the **domain-blind governance kernel**: it observes a
decision, judges its outcome against rules fixed and hashed before the
outcome was known, and records every step in a tamper-evident Postgres
hash-chained ledger with an independent twin witness. It knows nothing about
any particular application domain — a *cassette* supplies that, and the
kernel refuses any cassette that asks for a capability the kernel does not
provide.

## Where things are

| Area | Files |
|---|---|
| Observation & judgment | `episode.py`, `event_v1.py`, `outcome_v1.py` |
| Ledger, twin, attestation | `governance/ledger_postgres.py`, `twin_custody.py`, `twin_shipper.py`, `governance/authorized_by_attestation.py` |
| The one governed door | `governance_harness.py` (`GovernanceHarness`) |
| Cassette framework | `cassette_schema.py`, `cassette_capabilities.py`, `cassette_loader.py`, `cassette_interface.py`, `cassettes/` (reference: `mortgage_cassette.py`, `banking_cassette.py`; `ivr_cassette.py` is the full-capability example the harness deliberately refuses) |
| Regulatory / contract lenses | `regulatory_*.py` + `regulatory_cassettes/` (CFPB Reg B), `contract_*.py` + `contract_cassettes/` (DPA attestation), `bisg_estimator.py`, `sealed_demographic_channel.py` |
| Transmission queue | `queue_schema.py`, `rate_limiter_v2.py`, `lua/`, `sentinel_worker.py`, `api_server_v2.py` (Dockerfile default) |
| SAGE-K interpretation | `interpretation/`, `sage_k/` |
| Conservation-kernel boundary | `conservation/` (fail-closed: no durable state without conservation verification) |

## The IVR / Iceberg application is not here

The standalone contact-centre simulator and its `Domain/` `Sim/` `Engines/`
`Model/` `observe/` support tree, Twilio ingestion, the Claude governor
client, the queue/staffing/Bayes layer, and the resilient API server all live
in **[GSA-815](https://github.com/wking53214/GSA-815)**, which runs on this
kernel via `PYTHONPATH`. IVR/contact-centre decisioning was the original
proving ground for the architecture — see the root README — but the kernel
carries none of it.

## Running it

These are two separate things with separate setup.

**The governed lane** (ledger, redis, ingress and worker, in Docker). From this
directory:

```bash
export POSTGRES_PASSWORD="choose-one"
export ICEBERG_LEDGER_RUNTIME_PASSWORD="choose-another"
export ICEBERG_LEDGER_ATTESTATION_KEY="$(openssl rand -hex 32)"
export ICEBERG_API_KEYS="yourkey:yourname"
docker compose up -d
```

`docker compose up` refuses to start unless all four are set (see
[.env.example](.env.example) and [TROUBLESHOOTING.md](../TROUBLESHOOTING.md)).
Only the ingress is published, on port 8000. The database and Redis stay inside
Docker's network.

**The test suite** does not use that stack. It needs its own PostgreSQL (a role
and database both named `iceberg`) and Redis on localhost, and the twin tests
also need three OS accounts created by `sudo scripts/twin_ensure_services.sh`.
Without them, tests that need a database or Redis fail with "connection
refused" or are skipped. Setup commands are in the
[root CONTRIBUTING.md](../CONTRIBUTING.md) under "Local setup". Then, from this
directory:

```bash
pip install -r requirements.txt
python3 -m pytest .
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for environment variables and TLS/DB setup.
`.github/workflows/tests.yml` is the executable spec.

## License

Apache-2.0 — see [LICENSE](../LICENSE).
