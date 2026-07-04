# Row Count: 233

"""
main.py
-------

Top‑Level Description
---------------------
This is the canonical Iceberg runtime entrypoint. It performs:

1. Load configuration (application.yaml)
2. Initialize deterministic subsystems:
   - Routing graph
   - Simulator
   - Telemetry kernel
   - RL engines (PPO, MARL, Staffing RL)
   - Replay engine
   - Governance envelope
3. Start dashboard server
4. Expose runtime orchestrator

Best‑in‑Class Notes
-------------------
- Deterministic: No randomness.
- Governance‑Safe: Structural hashing + replay verification.
- Replay‑Friendly: Identical config → identical runtime.
"""

from __future__ import annotations
import yaml
import uvicorn

# Domain imports
from domain.build_graph import build_graph
from domain.telemetry import TelemetryKernel
from domain.replay import ReplayEngine
from domain.simulator import Simulator
from domain.rl_ppo import PPOEngine
from domain.rl_marl import MARLEngine
from domain.staffing_rl import StaffingRLEngine
from domain.governance_envelope import GovernanceEnvelope

# Server
from dashboard_server import create_dashboard_app


# ---------------------------------------------------------
# LOAD CONFIG
# ---------------------------------------------------------
def load_config(path: str = "application.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------
# INITIALIZE SUBSYSTEMS
# ---------------------------------------------------------
def initialize_runtime(config: dict):
    # Routing graph
    graph = build_graph()

    # Telemetry
    telemetry = TelemetryKernel()

    # Simulator
    simulator = Simulator(
        graph=graph,
        telemetry=telemetry,
        max_steps=config["simulator"]["max_steps_per_call"],
    )

    # RL engines
    ppo = PPOEngine(
        lr=config["rl"]["ppo"]["lr"],
        gamma=config["rl"]["ppo"]["gamma"],
        eps_clip=config["rl"]["ppo"]["eps_clip"],
    )

    marl = MARLEngine(
        lr=config["rl"]["marl"]["lr"],
        hidden=config["rl"]["marl"]["hidden"],
        agents=config["rl"]["marl"]["agents"],
    )

    staffing = StaffingRLEngine(
        lr=config["rl"]["staffing"]["lr"],
        delta_limit=config["rl"]["staffing"]["delta_limit"],
    )

    # Replay engine
    replay = ReplayEngine(
        ledger=telemetry.ledger,
        simulator=simulator,
    )

    # Governance envelope
    governance = GovernanceEnvelope(
        enable_structural_hash=config["governance"]["enable_structural_hash"],
        enable_replay_verification=config["governance"]["enable_replay_verification"],
        enable_seed_freeze=config["governance"]["enable_seed_freeze"],
    )

    return {
        "graph": graph,
        "telemetry": telemetry,
        "simulator": simulator,
        "ppo": ppo,
        "marl": marl,
        "staffing": staffing,
        "replay": replay,
        "governance": governance,
    }


# ---------------------------------------------------------
# START SERVER
# ---------------------------------------------------------
def start_server(config: dict, runtime: dict):
    app = create_dashboard_app(runtime)

    host = config["server"]["host"]
    port = config["server"]["port"]

    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    config = load_config()
    runtime = initialize_runtime(config)
    start_server(config, runtime)


if __name__ == "__main__":
    main()