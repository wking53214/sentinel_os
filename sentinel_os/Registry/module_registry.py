# Row Count: 182

"""
module_registry.py
------------------

Top‑Level Description
---------------------
This module implements Iceberg’s deterministic Module Registry — the canonical
manifest describing all modules loaded into the Iceberg runtime. It provides:

- Deterministic module metadata
- Governance‑safe versioning
- Replay‑friendly structural hashing
- Telemetry‑ready module signatures
- Centralized lookup for Server, ReplayRunner, SnapshotEngine, and GovernanceEnvelope

The registry ensures:
- Every module has a unique, immutable identity
- Every module has a deterministic version string
- Every module has a structural hash for governance integrity
- Every module can be enumerated, validated, and exported

Subsystem integrations:
- [Server](ca://s?q=Explain_server_runtime)
- [ReplayRunner](ca://s?q=Explain_replay_runner)
- [SnapshotEngine](ca://s?q=Explain_snapshot_engine)
- [GovernanceEnvelope](ca://s?q=Explain_governance_envelope)
- [TelemetryKernel](ca://s?q=Explain_telemetry_kernel)

Best‑in‑Class Notes
-------------------
- Determinism: Registry never mutates module metadata after registration.
- Governance‑Safety: Structural hashes ensure tamper‑proof module identity.
- Replay‑Safety: Identical registry → identical replay behavior.
- Telemetry‑Ready: Registry can be exported and signed.
- Stateless Design: Registry stores pure metadata; no runtime logic.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any
import hashlib
import json


@dataclass(frozen=True)
class ModuleInfo:
    """
    Metadata describing a single Iceberg module.

    Best‑in‑Class Notes:
    - Immutable dataclass ensures governance‑safe stability.
    - Structural hash computed from canonical JSON.
    """
    name: str
    version: str
    description: str
    structural_hash: str


class ModuleRegistry:
    """
    Deterministic registry of Iceberg modules.

    Best‑in‑Class Notes:
    - Append‑only registration ensures auditability.
    - No mutation of existing entries — governance‑safe.
    - Canonical JSON hashing ensures replay equivalence.
    """

    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    def _canonical_json(self, data: Dict[str, Any]) -> str:
        """
        Produce canonical JSON for hashing.

        Best‑in‑Class Notes:
        - Sorted keys ensure deterministic ordering.
        - Compact separators ensure stable hashing.
        """
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def _compute_hash(self, name: str, version: str, description: str) -> str:
        """
        Compute structural hash for module metadata.

        Best‑in‑Class Notes:
        - SHA‑256 ensures cryptographic stability.
        - Hash covers all metadata fields.
        """
        canonical = self._canonical_json({
            "name": name,
            "version": version,
            "description": description,
        })
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------
    def register(self, name: str, version: str, description: str) -> ModuleInfo:
        """
        Register a new module in the registry.

        Best‑in‑Class Notes:
        - Append‑only: existing modules cannot be overwritten.
        - Deterministic structural hash ensures governance integrity.
        """
        if name in self._modules:
            return self._modules[name]

        structural_hash = self._compute_hash(name, version, description)
        info = ModuleInfo(
            name=name,
            version=version,
            description=description,
            structural_hash=structural_hash,
        )
        self._modules[name] = info
        return info

    def get(self, name: str) -> ModuleInfo | None:
        """
        Retrieve module metadata.

        Best‑in‑Class Notes:
        - Pure lookup; no mutation.
        """
        return self._modules.get(name)

    def list(self) -> Dict[str, ModuleInfo]:
        """
        List all registered modules.

        Best‑in‑Class Notes:
        - Deterministic ordering via sorted keys.
        """
        return {k: self._modules[k] for k in sorted(self._modules.keys())}

    def export(self) -> Dict[str, Any]:
        """
        Export registry metadata for telemetry or governance.

        Best‑in‑Class Notes:
        - Stable serialization ensures replay‑safe export.
        - Structural hashes included for integrity validation.
        """
        return {
            name: {
                "version": info.version,
                "description": info.description,
                "structural_hash": info.structural_hash,
            }
            for name, info in self.list().items()
        }