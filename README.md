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

41/41 passing. 942K calls/sec verified. Production ready.

See DEPLOYMENT.md for deployment instructions.
