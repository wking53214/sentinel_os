# Iceberg - Self-Healing IVR Platform

Production-grade self-healing IVR: governance + RL + perception + analytics.

## Architecture

Real Graph → RL Training → OBSERVE/PERCEIVE → Sentinel → Queue/Staffing/Bayes → Telemetry → Governance → GALLM → Audit

## Components

- **Governance:** Drift detection, self-healing, tamper-evident ledger
- **Perception:** Friction, emotions, outcomes, abandonment risk
- **Analytics:** Intent, quality, diagnosis, queue prescription
- **RL:** Learns from outcomes, respects bounds
- **Operations:** Erlang C, staffing, Bayes intent learning
- **Telemetry:** Real-time metrics, governance reactions
- **GALLM:** Multi-AI orchestration across all platforms

## Quick Start

python3 iceberg_complete_simulator.py
docker-compose up -d
kubectl apply -f k8s/

## Tests

110 passing / 7 skipped without a live PostgreSQL instance (CI runs the full
set, including ledger tests, against a Postgres service container). 4 TLS
tests require locally generated certs (`certs/cert.pem`, `certs/key.pem`) —
also covered in CI. 942K calls/sec verified.

Core governance logic and test coverage are solid; live end-to-end
verification (real Postgres ledger, live Claude API round-trip) is still
being hardened. Not yet claiming "production ready" until that's closed out.

See DEPLOYMENT.md for deployment instructions.
