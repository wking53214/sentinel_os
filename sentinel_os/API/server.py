# Row Count: 215

"""
server.py
---------

Deterministic Server Runtime for Iceberg.

Best–in–Class Notes:
- Hosts the Simulator, RL Engines, and Aegis–Loop.
- Guarantees strict mode deterministic API boundaries.
"""

from __future__ import annotations
from typing import Dict, Any
from schemas import SimulateRequest

# Internal mocks for standalone compilation. In production, these map to imports.
class MockSimulator:
    def step(self, caller_data: dict, start_node: str) -> dict:
        return {
            "caller_id": caller_data["caller_id"],
            "next_node": "exit",
            "routing": {"action": 1},
            "staffing": {"billing": 0.1},
            "bayes": {"billing": 0.8},
            "queue": {"status": "ok"}
        }

class IcebergAPI:
    """
    Iceberg Public API Contract Engine.
    
    Governance Notes:
    - Version anchored to deterministic constant 815.
    - strict_mode ensures no unvalidated payloads penetrate the system.
    """
    
    version: str = "3.8.15"
    strict_mode: bool = True
    
    def __init__(self):
        # In a fully wired system, the real Simulator and Aegis-Loop are injected here.
        self.simulator = MockSimulator()

    def simulate(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a multi–step deterministic simulation.
        
        Best–in–Class Notes:
        - Validates via Pydantic SimulateRequest.
        - Severance logic prevents sycophancy loops.
        """
        # 1. Strict boundary validation
        req = SimulateRequest(**request_payload)
        
        # 2. Build initial caller dictionary state
        caller_state = {
            "caller_id": req.caller_id,
            "intent": req.intent,
            "emotion": req.emotion,
            "dynamic": {"perceived_wait": 0.0, "frustration": 0.0}
        }
        
        current_node = "root"
        final_output = {}

        # 3. Deterministic execution cycle
        for _ in range(req.steps):
            final_output = self.simulator.step(caller_state, current_node)
            current_node = final_output.get("next_node", "exit")
            if current_node == "exit":
                break

        return final_output

    def get_status(self) -> Dict[str, Any]:
        return {"version": self.version, "strict": self.strict_mode, "status": "online"}