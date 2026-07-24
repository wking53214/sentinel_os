# Iceberg: Self-Healing IVR Platform

**An AI-powered customer service platform that learns from every call, fixes its own problems, and makes smarter staffing decisions in real time.** Stop losing customers to frustrating phone trees and long waits—Iceberg learns what works and adapts automatically.

---

## What Problem Does This Solve?

Traditional IVR systems are rigid, inflexible, and frustrating:
- ❌ Long wait times with no smart routing
- ❌ Callers abandon calls and get angry
- ❌ No learning from past mistakes
- ❌ Manual configuration changes are slow and risky

**Iceberg fixes all of this:**
- ✅ Learns the best routing from real call outcomes
- ✅ Detects when something's broken and fixes it automatically
- ✅ Understands caller emotions, intent, and abandonment risk
- ✅ Recommends optimal staffing in real time
- ✅ Audits every decision with tamper-proof records

---

## Features at a Glance

| Feature | Status | Details |
|---------|--------|---------|
| **Self-Healing Governance** | ✅ Ready | Detects drift, auto-corrects, prevents tampering |
| **Reinforcement Learning** | ✅ Ready | Learns optimal call routing from outcomes |
| **Perception Engine** | ✅ Ready | Detects friction, emotions, abandonment risk |
| **Queue & Staffing** | ✅ Ready | Erlang C + Bayesian intent prediction |
| **Real-Time Analytics** | ✅ Ready | Intent detection, quality scoring, diagnostics |
| **Multi-AI Orchestration** | ✅ Ready | Coordinates Claude + domain models |
| **Tamper-Evident Ledger** | ✅ Ready | PostgreSQL-backed immutable audit log |
| **End-to-End Testing** | ✅ Ready | Full suite passing on real CI (Postgres 16 + Redis) |
| **Production Deployment** | 🔄 In Progress | Docker & Kubernetes configs ready; live testing ongoing |

**Performance:** Not yet benchmarked end-to-end against the real API/ledger path — see [Load Testing & Performance](#load-testing--performance) below.

---

## Quick Start (5 Minutes)

### Option 1: Standalone Simulator (Easiest)
```bash
# No setup needed beyond Python 3.8+
python3 sentinel_os/iceberg_complete_simulator.py
```
This runs the complete system in memory with simulated call data. Perfect for understanding how everything works.

### Option 2: Full Stack with Docker
```bash
cd sentinel_os
docker-compose up -d
# Services available at:
# - API: http://localhost:8000
# - Grafana: http://localhost:3000
# - PostgreSQL: localhost:5432
```

### Option 3: Kubernetes Deployment
```bash
kubectl apply -f sentinel_os/k8s/
# See DEPLOYMENT.md for full instructions
```

---

## Prerequisites

**For Quick Start (Simulator):**
- Python 3.8 or higher
- pip (Python package manager)

**For Docker:**
- Docker and Docker Compose
- ~2GB free disk space

**For Full Stack + Tests:**
- Python 3.8+
- PostgreSQL 13+
- Docker and Docker Compose
- Kubernetes cluster (optional, for k8s deployment)

---

## Architecture (Plain English)

**Here's how Iceberg works:**

1. **Real calls come in** → System observes what happens
2. **Learning engine analyzes** → What worked? What didn't?
3. **AI recommends changes** → Better routing, staffing predictions
4. **System applies changes** → Calls automatically route smarter
5. **Governance watches** → Detects if anything breaks or looks wrong
6. **Auto-healing kicks in** → Fixes problems before humans notice
7. **Audit log records everything** → Tamper-proof history of all decisions

**Technical Architecture:**
```
Real Call Graph 
    ↓
RL Training (learns optimal policies)
    ↓
OBSERVE/PERCEIVE (detect emotions, intent, friction)
    ↓
Sentinel Core (governance + decision logic)
    ↓
Queue/Staffing/Bayes (operational predictions)
    ↓
Telemetry Pipeline (real-time metrics)
    ↓
Governance Engine (drift detection, self-healing)
    ↓
GALLM Coordinator (multi-AI orchestration)
    ↓
Audit Ledger (immutable record)
```

---

## Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/wking53214/sentinel_os.git
cd sentinel_os
```

### 2. Install Dependencies
```bash
pip install -r sentinel_os/requirements.txt
```

### 3. Run Tests
```bash
# Run core tests (no external dependencies)
python3 -m pytest sentinel_os/Tests/ -v

# Full test suite (requires PostgreSQL)
# See DEPLOYMENT.md for test setup
```

### 4. Start the Simulator
```bash
python3 sentinel_os/iceberg_complete_simulator.py
```

### 5. Explore the Code
- **Core Logic:** `sentinel_os/sentinel_core.py`
- **Governance:** `sentinel_os/governance/`
- **Analytics:** `sentinel_os/observe_perceive_core.py`
- **Operations:** `sentinel_os/queue_staffing_bayes_integration.py`
- **API Server:** `sentinel_os/api_server.py`
- **Standalone simulator's supporting modules:** `sentinel_os/Domain/`,
  `Engines/`, `Model/`, `Sim/`, `observe/` (each has its own README) --
  used only by `iceberg_complete_simulator.py` and its tests, not by the
  production governance path above.

---

## Known Limitations & What's Not Ready Yet

**Currently verified:**
- Full suite passing on real CI (Postgres 16 + Redis), 0 failed
- Docker Compose full-stack deployment verified live (ledger connected, health checks pass)
- Standalone in-memory simulator verified live

**Still open** (see `governance/README.md` and `docs/CHANGELOG.md` for detail):
- Bias testing and adverse-action specificity for governance decisions
- `test_twin_live.py` needs a native (non-Docker) Postgres install sharing a Unix socket with the test process, for real OS-identity peer-auth boundaries (`sentinelsvc`/`twincustomer`/`twincustodian` -- provisioned by `scripts/twin_ensure_services.sh`, now committed). Passes 383/383 alongside the rest of the suite locally or in any environment with a native Postgres, verified twice back-to-back from a clean state. Still excluded from the GitHub Actions workflow specifically, because its `services: postgres:` block is a separate Docker container reachable only over TCP -- there's no Unix socket to share, so peer auth structurally can't work there. Closing that gap means giving this CI job a natively-installed Postgres instead of the services: container, a separable follow-up.

**Status:** Core governance logic, test coverage, and both primary deployment paths (Docker Compose, standalone simulator) are live-verified. Production deployment against real call systems has not been attempted.

**Timeline:** See issues and milestones for progress updates.

---

## Documentation

- 📖 [DEPLOYMENT.md](sentinel_os/DEPLOYMENT.md) — How to deploy to production
- 📋 [COMPLIANCE.md](sentinel_os/COMPLIANCE.md) — Compliance and audit details
- 🔍 [AUDIT_PLAYBOOK.md](sentinel_os/AUDIT_PLAYBOOK.md) — How to audit system decisions
- 🏗️ [MODEL_CARD.md](sentinel_os/MODEL_CARD.md) — ML model details and limitations
- ⚙️ [structure.txt](sentinel_os/structure.txt) — Directory structure guide

---

## Load Testing & Performance

```bash
# Run load tests
python3 sentinel_os/load_test_live.py  # Against live API
python3 sentinel_os/load_test.py       # Against in-memory drift-detection math
```

`load_test.py` exercises the drift-detection/self-heal functions directly
in memory (no API, ledger, or governance call in the loop), which is where
past "942K calls/sec" figures came from -- that number reflects raw
in-memory function throughput, not real governed call processing.
`load_test_live.py` hits the actual API server and is the more meaningful
number for real-world capacity, but no committed benchmark report exists
yet for that path. Treat any throughput figure here as directional until
a real end-to-end benchmark is run and linked.

---

## Contributing

We're actively developing this. Here's how you can help:

1. **Test the simulator** and report issues
2. **Run the test suite** and help close any remaining skipped or excluded tests
3. **Test live deployment** with PostgreSQL
4. **Improve documentation** with examples and troubleshooting
5. **File issues** for bugs or features you'd like to see

See open issues for areas needing help.

---

## License

No license has been chosen yet. Until one is added, default copyright
applies -- all rights reserved, no permission is granted to use, copy,
modify, or distribute this code. If you intend for others to use this
project, pick a license (e.g. MIT, Apache-2.0) and add a LICENSE file.

---

## Questions?

- 📧 File an issue on GitHub
- 📚 Check the documentation files listed above
- 💬 Start a discussion for architecture questions

---

**Made with ❤️ for better customer experiences.**
