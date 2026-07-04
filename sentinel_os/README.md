# Row Count: 214

# Iceberg Runtime 3.x

Iceberg is a deterministic, governance‑safe simulation and routing engine for
customer‑support IVR systems. It provides:

- A deterministic Simulator
- PPO + MARL routing engines
- Staffing RL optimizer
- Bayesian intent engine
- Telemetry kernel
- Replay engine
- Governance envelope
- Dashboard server
- Unified client + CLI

Iceberg guarantees:
- Zero randomness in routing decisions
- Replay‑friendly event streams
- Governance‑safe structural hashing
- Deterministic graph traversal
- JSON‑safe snapshots across all subsystems

---

## Architecture Overview

Iceberg is composed of the following subsystems:

### **Routing Graph**
Deterministic DAG describing IVR menu flow.  
Built via `build_graph()` and validated by `test_graph_integrity.py`.

### **Simulator**
Executes caller journeys through the routing graph.  
Produces:
- CallerState snapshots  
- QueueState transitions  
- Telemetry events  

### **RL Engines**
- **PPOEngine** — single‑agent routing  
- **MARLEngine** — multi‑agent joint routing  
- **StaffingRLEngine** — staffing deltas for queue optimization  

All RL engines are deterministic and governance‑safe.

### **Telemetry Kernel**
Append‑only event ledger used for:
- Dashboard streaming  
- Replay reconstruction  
- Governance verification  

### **Replay Engine**
Reconstructs full caller journeys from telemetry ledger.  
Produces:
- ReplayBundle  
- Structural hash  
- Event timeline  

### **Governance Envelope**
Provides:
- Structural hash verification  
- Replay equivalence checks  
- Seed‑freeze enforcement  

### **Dashboard Server**
FastAPI server exposing:
- `/dashboard/export`  
- `/dashboard/queues`  
- `/dashboard/callers`  
- `/dashboard/telemetry`  
- `/dashboard/replay`  
- `/dashboard/rl`  

Serves HTML pages:
- `index.html`  
- `queues.html`  
- `callers.html`  
- `telemetry.html`  
- `replay.html`  
- `rl.html`  

### **Client + CLI**
- `client.py` — Python API client  
- `cli.py` — command‑line interface  

---

## Directory Layout