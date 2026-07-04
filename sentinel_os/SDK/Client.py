# Row Count: 143

"""
client.py
---------

Deterministic Python client for interacting with the Iceberg Dashboard Server.

This client provides:
- Snapshot retrieval
- Queue + caller views
- Telemetry stream access
- Replay execution
- RL episode execution
- Governance structural hash retrieval

Best‑in‑Class Notes:
- Deterministic: No randomness in requests or parsing.
- Governance‑Safe: JSON‑safe payloads only.
- Replay‑Friendly: Identical server → identical client output.
"""

from __future__ import annotations
import requests
from typing import Any, Dict, List


class IcebergClient:
    """
    Canonical Iceberg dashboard client.

    Parameters
    ----------
    base_url : str
        Base URL of the dashboard server, e.g. "http://localhost:8000"
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    # ---------------------------------------------------------
    # INTERNAL REQUEST WRAPPER
    # ---------------------------------------------------------
    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        res = requests.get(url)
        res.raise_for_status()
        return res.json()

    # ---------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return self._get("/dashboard/export")["snapshot"]

    # ---------------------------------------------------------
    # STRUCTURAL HASH
    # ---------------------------------------------------------
    def structural_hash(self) -> str:
        return self._get("/dashboard/export")["structural_hash"]

    # ---------------------------------------------------------
    # QUEUES
    # ---------------------------------------------------------
    def queues(self) -> Dict[str, Any]:
        return self._get("/dashboard/queues")

    # ---------------------------------------------------------
    # CALLERS
    # ---------------------------------------------------------
    def callers(self) -> Dict[str, Any]:
        return self._get("/dashboard/callers")

    # ---------------------------------------------------------
    # TELEMETRY
    # ---------------------------------------------------------
    def telemetry(self) -> List[Dict[str, Any]]:
        return self._get("/dashboard/telemetry")

    # ---------------------------------------------------------
    # REPLAY
    # ---------------------------------------------------------
    def replay(self) -> Dict[str, Any]:
        return self._get("/dashboard/replay")

    def replay_events(self) -> List[Dict[str, Any]]:
        return self._get("/dashboard/replay/events")

    # ---------------------------------------------------------
    # RL
    # ---------------------------------------------------------
    def rl(self) -> Dict[str, Any]:
        return self._get("/dashboard/rl")

    def rl_episode(self) -> Dict[str, Any]:
        return self._get("/dashboard/rl/episode")