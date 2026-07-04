# Row Count: 241

# Iceberg Engineering Curriculum

This curriculum provides a complete, deterministic learning path for engineers
working on the Iceberg 3.x runtime. It covers:

- Core architecture
- Routing graph design
- Simulator internals
- RL engines (PPO, MARL, Staffing RL)
- Telemetry kernel
- Replay engine
- Governance envelope
- Dashboard server
- Testing + verification
- Production deployment

The curriculum is divided into **four phases**, each with clear learning goals,
hands‑on exercises, and mastery checkpoints.

---

## Phase 1 — Foundations

### Objective
Understand Iceberg’s deterministic architecture and governance principles.

### Topics
- Iceberg Runtime Overview  
- Determinism vs stochastic systems  
- Governance envelope  
- Structural hashing  
- Replay equivalence  
- JSON‑safe model surfaces  
- Directory layout  
- application.yaml configuration  

### Exercises
- Read `README.md`  
- Inspect `application.yaml`  
- Run `python main.py`  
- Load dashboard at `http://localhost:8000`  
- Use CLI:  
  - `python cli.py snapshot`  
  - `python cli.py queues`  
  - `python cli.py replay`  

### Mastery Check
You should be able to explain:
- Why Iceberg must be deterministic  
- What structural hashing protects  
- How replay detects drift  
- How the dashboard consumes snapshots  

---

## Phase 2 — Core Systems

### Objective
Master the Simulator, Routing Graph, Telemetry Kernel, and Replay Engine.

### Topics
- `build_graph.py` — deterministic DAG  
- `Simulator` — caller journey execution  
- `CallerState` + `QueueState`  
- `TelemetryKernel` — append‑only ledger  
- `ReplayEngine` — deterministic reconstruction  
- Snapshot semantics  
- Event ordering guarantees  

### Exercises
- Visualize graph with `test_graph_integrity.py`  
- Add a new node to the routing graph  
- Run a simulation with multiple callers  
- Inspect telemetry stream in `telemetry.html`  
- Trigger replay via CLI  
- Compare structural hashes before/after graph changes  

### Mastery Check
You should be able to:
- Build a new routing graph node  
- Explain how telemetry events become replay events  
- Describe how snapshots are constructed  
- Identify invalid graph structures  

---

## Phase 3 — Reinforcement Learning Engines

### Objective
Understand and extend Iceberg’s deterministic RL stack.

### Topics
- PPOEngine  
- MARLEngine  
- StaffingRLEngine  
- Deterministic action selection  
- Governance‑safe RL outputs  
- RL episode replay  
- RL dashboard integration  

### Exercises
- Run `python cli.py rl`  
- Trigger an RL episode  
- Inspect PPO/MARL outputs in `rl.html`  
- Modify PPO hyperparameters in `application.yaml`  
- Add a new MARL agent  
- Validate RL determinism using structural hash  

### Mastery Check
You should be able to:
- Explain PPO vs MARL in Iceberg  
- Describe how RL outputs affect routing  
- Run deterministic RL episodes  
- Extend RL engines without breaking governance  

---

## Phase 4 — Dashboard, API, and Governance

### Objective
Master the full runtime stack, including dashboard server and governance envelope.

### Topics
- `dashboard_server.py`  
- `/dashboard/*` endpoints  
- HTML pages:  
  - index  
  - queues  
  - callers  
  - telemetry  
  - replay  
  - rl  
- GovernanceEnvelope  
- Replay verification  
- Structural hash enforcement  
- Production deployment patterns  

### Exercises
- Add a new dashboard page  
- Add a new API endpoint  
- Implement a governance rule  
- Run replay verification  
- Deploy Iceberg to a staging environment  
- Add a new test suite under `tests/`  

### Mastery Check
You should be able to:
- Build new dashboard visualizations  
- Extend API surface safely  
- Enforce governance constraints  
- Deploy Iceberg deterministically  

---

## Capstone Project — Build a New Deterministic Module

### Objective
Demonstrate mastery by designing a new Iceberg subsystem.

### Requirements
- Deterministic behavior  
- JSON‑safe snapshots  
- Structural hash support  
- Telemetry integration  
- Replay compatibility  
- Dashboard visualization  
- CLI support  
- Test suite  

### Examples
- New Bayesian intent engine  
- New queue optimizer  
- New caller emotion model  
- New MARL coordination strategy  

---

## Certification Checklist

You are considered **Iceberg‑certified** when you can:

- [ ] Explain determinism and governance  
- [ ] Build and validate routing graphs  
- [ ] Run and inspect simulations  
- [ ] Understand telemetry and replay  
- [ ] Extend RL engines safely  
- [ ] Add dashboard pages  
- [ ] Add CLI commands  
- [ ] Write structural‑hash‑safe modules  
- [ ] Deploy Iceberg in production  
- [ ] Write full test suites  

---

## Recommended Reading

- Iceberg `README.md`  
- `application.yaml`  
- `main.py`  
- `dashboard_server.py`  
- `replay.py`  
- `telemetry.py`  
- RL engine modules  
- Governance envelope  

---

## Versioning

This curriculum is versioned alongside Iceberg Runtime 3.x.