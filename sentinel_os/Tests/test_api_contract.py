# Row Count: 241

"""
test_api_contract.py
--------------------

Deterministic regression tests for Iceberg's public API contract.

These tests guarantee:
- Stable request/response envelopes
- Deterministic API behavior for identical inputs
- JSON-safe responses
- Structural-hash stability
- Replay-friendly API evolution
- No drift in required fields or schema

Best-in-Class Notes
-------------------
- Deterministic: No randomness in API output.
- Governance-Safe: Structural hash detects drift.
- Replay-Friendly: Identical requests → identical responses.
"""

import json
import hashlib
import pytest

from api.server import IcebergAPI
from domain.simulator import Simulator
from domain.build_graph import build_graph
from domain.telemetry import TelemetryKernel
from domain.CallerState import CallerState


# ---------------------------------------------------------
# Structural hash utility
# ---------------------------------------------------------
def structural_hash(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# TEST 1 — API initializes deterministically
# ---------------------------------------------------------
def test_api_initialization():
    api1 = IcebergAPI()
    api2 = IcebergAPI()

    assert api1.version == api2.version
    assert api1.strict_mode == api2.strict_mode


# ---------------------------------------------------------
# TEST 2 — Deterministic /simulate response
# ---------------------------------------------------------
def test_simulate_deterministic():
    api = IcebergAPI()

    req = {
        "caller_id": "c1",
        "intent": "billing",
        "steps": 3
    }

    out1 = api.simulate(req)
    out2 = api.simulate(req)

    assert out1 == out2, "API /simulate drift detected"


# ---------------------------------------------------------
# TEST 3 — JSON-safe API response
# ---------------------------------------------------------
def test_json_safe_response():
    api = IcebergAPI()

    req = {
        "caller_id": "c-json",
        "intent": "tech",
        "steps": 2
    }

    out = api.simulate(req)

    try:
        json.dumps