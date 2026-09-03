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

```bash
pip install -r requirements.txt
docker compose up -d          # governed lane: ledger + redis + ingress + worker
python3 -m pytest Tests/ -v   # ~698 tests; ledger/queue tests need Postgres + Redis
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for environment variables and TLS/DB setup.

## License

Apache-2.0 — see [LICENSE](../LICENSE).
