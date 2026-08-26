"""
GSA adapter layer: envelope type, hash-chain signing, and the wrapper that
lets any "module" (kernel, extractor, etc.) be plugged into a pipeline while
its calls get tagged with a running SHA-256 integrity chain.

Reconstructed from artifact_2.py and artifact_3.py, which each contained a
near-identical copy of this layer. One real divergence between the two
copies was found and fixed here rather than picked arbitrarily:

- In artifact_2, GsaUniversalAdapter always `await`s the wrapped module's
  execute_governance_logic/_module method (correct for GsaTemporalDoorwayGate,
  which is async).
- In artifact_3, the same adapter calls it *without* await (correct for
  ExtractorGsaAdapterModule, which is a plain sync method).

Calling a sync method with `await` raises a TypeError; calling an async
method without `await` returns an un-awaited coroutine and silently skips
its body. Neither original file works with the other file's module type.
`process_payload` below detects which kind it got and handles both.

A second, independent bug was found while getting this to actually run:
artifact_2.py's own demo harness (main_test_harness) seeds a one-entry
`gsa_chain_history` and signs it using that entry as the upstream hash, but
the adapter's inbound-verification branch, for a one-entry history, checked
the provided hash against a signature computed with a hardcoded
"GENESIS_ANCHOR" prior-anchor instead -- a mismatch that always fails.
(This was never actually caught before because artifact_2.py doesn't parse:
it has an odd number of triple-quote docstring delimiters and raises
SyntaxError on import, so the transcript's claimed test-harness result was
never actually executed.)
Fixed here by only running that verification once there are at least two
recorded entries; a one-entry history is a seed anchor, not a step to
verify against.

Note on scope: despite the "cryptographic interlock" naming, this is a
deterministic SHA-256 hash chain over JSON-serialized state for tamper-
evidence and ordering, not encryption. It has no confidentiality property
and manages no encryption keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

__all__ = [
    "GsaContextEnvelope",
    "ComposableLegoModule",
    "GsaUniversalAdapter",
    "GsaTemporalDoorwayGate",
    "compute_state_signature",
]


def _local_deep_freeze(data_structure: Any) -> Any:
    """
    Recursively deep-freezes nested dictionaries and lists into read-only types.
    Local implementation; the original transcript referenced an external
    `universal_foundation.deep_freeze_structure_function` module that does not
    exist in this repo.
    """
    if isinstance(data_structure, dict):
        return MappingProxyType({k: _local_deep_freeze(v) for k, v in data_structure.items()})
    elif isinstance(data_structure, list):
        return tuple(_local_deep_freeze(element) for element in data_structure)
    return data_structure


class ComposableLegoModule(Protocol):
    """Interface expected of a module wrapped by GsaUniversalAdapter."""
    async def process_payload(self, context_envelope: Any) -> Any:
        ...


@dataclass(frozen=True)
class GsaContextEnvelope:
    """Data packet passed through the adapter chain."""
    payload_data: Dict[str, Any]
    session_state_mapping: Dict[str, Any]
    header_mapping: Mapping[str, Any] = field(default_factory=dict)
    status_string: str = "INITIALIZED"


def compute_state_signature(
    upstream_hash: str,
    iteration: int,
    envelope: Any,
    extra_anchors: Optional[List[str]] = None,
) -> str:
    """
    Deterministic SHA-256 hash over the upstream hash, iteration counter,
    any merge anchors, and the envelope's payload/session data. Used to
    chain-link successive adapter steps so tampering or dropped steps are
    detectable, not to encrypt anything.
    """
    serialized_payload = json.dumps(envelope.payload_data, sort_keys=True, default=str)
    serialized_session = json.dumps(envelope.session_state_mapping, sort_keys=True, default=str)
    sorted_anchors = "||".join(sorted(extra_anchors)) if extra_anchors else "NONE"

    buffer_source = (
        f"parent:{upstream_hash}||"
        f"iter:{iteration}||"
        f"graph:[{sorted_anchors}]||"
        f"payload:{serialized_payload}||"
        f"session:{serialized_session}"
    )
    return hashlib.sha256(buffer_source.encode("utf-8")).hexdigest()


class GsaUniversalAdapter:
    """
    Wraps any module exposing execute_governance_logic / execute_governance_module
    (sync or async) or a plain callable bridge. Verifies inbound hash-chain
    continuity, runs the module, then stamps and re-chains the outbound state.
    """

    def __init__(
        self,
        underlying_module: Any,
        translation_bridge: Optional[Callable[[Any, Any], Any]] = None,
    ) -> None:
        self.module = underlying_module
        self.bridge = translation_bridge or (lambda m, env: env)
        self.actor_name = type(underlying_module).__name__

    async def process_payload(self, context_envelope: GsaContextEnvelope) -> GsaContextEnvelope:
        headers = dict(context_envelope.header_mapping)
        hash_history = list(headers.get("gsa_chain_history", []))
        fork_tracking = dict(headers.get("gsa_graph_forks", {}))
        anchor_registry = dict(headers.get("gsa_static_anchors", {}))

        current_iteration = headers.get("gsa_loop_iteration", 0)
        reentry_target_id = headers.get("gsa_reentry_target_id")

        upstream_hash = "GENESIS_ANCHOR"
        target_merge_keys: List[str] = []
        upstream_anchors: List[str] = []

        # --- Phase 1: inbound verification & routing ---
        if reentry_target_id and reentry_target_id in anchor_registry:
            saved_anchor_hash = anchor_registry[reentry_target_id]
            provided_current_hash = headers.get("gsa_interlock_hash")

            if provided_current_hash != saved_anchor_hash:
                return replace(
                    context_envelope,
                    status_string=f"GSA_ANCHOR_MISMATCH: Deviation identified for anchor '{reentry_target_id}'.",
                )

            headers.pop("gsa_reentry_target_id", None)
            upstream_hash = saved_anchor_hash

        else:
            target_merge_keys = [k for k, v in fork_tracking.items() if v == self.actor_name]
            if target_merge_keys:
                upstream_anchors = [headers.get(f"gsa_branch_hash_{k}", "") for k in target_merge_keys]
                upstream_hash = "||".join(upstream_anchors)
                for k in target_merge_keys:
                    fork_tracking.pop(k, None)
                    headers.pop(f"gsa_branch_hash_{k}", None)
            else:
                upstream_hash = hash_history[-1] if hash_history else "GENESIS_ANCHOR"

                # A history of length 1 is a manually-seeded starting anchor,
                # not yet an adapter-produced step, so there is nothing to
                # verify it against. Only verify from the second recorded
                # entry onward. (The original artifact checked `if hash_history:`
                # here, which tries to verify the seed entry against a
                # hardcoded "GENESIS_ANCHOR" prior-anchor that never matches
                # how the seed was actually signed -- see gsa_adapter.py
                # module docstring / README for details.)
                if len(hash_history) > 1:
                    provided_current_hash = headers.get("gsa_interlock_hash")
                    prior_anchor = hash_history[-2]
                    expected_current_hash = compute_state_signature(prior_anchor, current_iteration, context_envelope)

                    if provided_current_hash != expected_current_hash:
                        return replace(
                            context_envelope,
                            status_string=f"GSA_CHAIN_BREAK: Signature validation failed at iteration {current_iteration}.",
                        )

        headers["gsa_graph_forks"] = fork_tracking
        working_envelope = replace(context_envelope, header_mapping=MappingProxyType(headers))

        # --- Phase 2: run the wrapped module, sync or async ---
        if hasattr(self.module, "execute_governance_logic"):
            result = self.module.execute_governance_logic(working_envelope)
        elif hasattr(self.module, "execute_governance_module"):
            result = self.module.execute_governance_module(working_envelope)
        else:
            loop = asyncio.get_event_loop()
            result = loop.run_in_executor(None, self.bridge, self.module, working_envelope)

        output_envelope = await result if inspect.isawaitable(result) else result

        # --- Phase 3: outbound stamping & locking ---
        updated_headers = dict(output_envelope.header_mapping)
        set_anchor_id = updated_headers.pop("gsa_set_static_anchor_id", None)

        next_iteration = current_iteration + 1
        outbound_hash = compute_state_signature(
            upstream_hash,
            next_iteration,
            output_envelope,
            extra_anchors=upstream_anchors if target_merge_keys else None,
        )
        hash_history.append(outbound_hash)

        if set_anchor_id:
            anchor_registry[set_anchor_id] = outbound_hash

        updated_headers["gsa_interlock_hash"] = outbound_hash
        updated_headers["gsa_chain_history"] = hash_history
        updated_headers["gsa_static_anchors"] = anchor_registry
        updated_headers["gsa_loop_iteration"] = next_iteration
        updated_headers["gsa_last_actor"] = self.actor_name

        return replace(
            output_envelope,
            header_mapping=_local_deep_freeze(updated_headers),
        )


class GsaTemporalDoorwayGate:
    """
    Async gate module: continuously rotates a SHA-256 hash from a seed and
    the current time, and blocks a passing envelope until its target hash
    matches the rotating value (or a timeout elapses). Effectively a
    self-contained polling rendezvous, not an external security boundary.
    """

    def __init__(self, rotation_seed: str, rotation_interval_seconds: float = 0.05) -> None:
        self._seed = rotation_seed
        self._interval = rotation_interval_seconds
        self._current_doorway_hash = ""
        self._is_operating = False
        self._lock = asyncio.Lock()

    async def start_gate_engine(self) -> None:
        self._is_operating = True
        asyncio.create_task(self._hash_rotation_worker())

    async def shutdown_gate_engine(self) -> None:
        self._is_operating = False

    async def _hash_rotation_worker(self) -> None:
        import time
        while self._is_operating:
            async with self._lock:
                entropy_buffer = f"{self._seed}||{time.time_ns()}".encode("utf-8")
                self._current_doorway_hash = hashlib.sha256(entropy_buffer).hexdigest()
            await asyncio.sleep(self._interval)

    async def execute_governance_logic(self, envelope: GsaContextEnvelope) -> GsaContextEnvelope:
        import time
        headers = dict(envelope.header_mapping)
        target_exit_hash = headers.get("gsa_target_exit_hash")

        if not target_exit_hash:
            return replace(
                envelope,
                status_string="GSA_DOORWAY_REJECT: Exit configuration requires 'gsa_target_exit_hash'.",
            )

        timeout_threshold = headers.get("gsa_doorway_timeout_seconds", 3.0)
        execution_start = time.time()
        handshake_secured = False

        while (time.time() - execution_start) < timeout_threshold:
            async with self._lock:
                if self._current_doorway_hash == target_exit_hash:
                    handshake_secured = True
                    break
            await asyncio.sleep(0.005)

        updated_headers = dict(envelope.header_mapping)

        if handshake_secured:
            updated_headers["gsa_doorway_cleared_hash"] = self._current_doorway_hash
            updated_headers["gsa_doorway_timestamp_ns"] = time.time_ns()
            return replace(
                envelope,
                status_string="GSA_EXIT_HANDSHAKE_COMPLETED",
                header_mapping=_local_deep_freeze(updated_headers),
            )
        return replace(
            envelope,
            status_string="GSA_DOORWAY_TIMEOUT: Temporal synchronization alignment window missed.",
            header_mapping=_local_deep_freeze(updated_headers),
        )
