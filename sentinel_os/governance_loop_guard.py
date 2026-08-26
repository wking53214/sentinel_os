"""governance_loop_guard.py -- detects a governance decision repeating
itself, a failure mode distinct from an outright API/transport failure.

PROVENANCE: salvaged from the STRIDE repo (stride-formatted-audited.py)
before its deletion from GitHub, 2026-08-20. STRIDE contained two
self-contained, working pieces judged worth keeping when the rest of the
repo was audited and found either inferior to CITADEL's own recovered
source or actively misrepresenting what it did. Only the first of those
two pieces -- output-loop detection / bounded retry lifecycle -- is
brought in here, since it's the one with a concrete, currently-
unprotected call site in this repo (production_harness.py's Claude
governor call, see the integration there). The second piece
(queue-depth backpressure) has no identified use site yet and was left
out rather than added speculatively; it remains available if a real
need for it turns up.

Class/method names are kept as in the original salvage -- they're
already generic and descriptive, not STRIDE-specific branding.

WHY THIS IS A DIFFERENT CHECK FROM circuit_breaker.py's CLAUDE BREAKER
------------------------------------------------------------------------
claude_breaker (production_harness.py) only sees what safety_check()
raises or explicitly flags via its is_failure predicate (a
"transport_error:"-prefixed reasoning string) -- a real API/network
failure. It has no way to notice a syntactically successful, correctly-
parsed response that happens to repeat a prior call's exact reasoning
text: not an error, but a plausible signal of a stuck or degenerate
governor response. That is what this module catches instead.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class EngineState:
    seen_outputs: Deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    last_output_hash: Optional[str] = None
    last_timestamp: Optional[float] = None
    retry_counter: int = 0


class PipelineStateEngine:
    """Monitors output history to detect generation loops and manage
    a bounded retry lifecycle."""

    def __init__(self, max_retries: int = 5, max_history: int = 1000):
        self.state = EngineState(seen_outputs=deque(maxlen=max_history))
        self.max_retries = max_retries

    def evaluate_integrity(self, output: str) -> bool:
        if not output or not output.strip():
            return False
        return True

    def check_loop_condition(self, output: str) -> bool:
        return output in self.state.seen_outputs

    def record_state(self, output: str) -> None:
        self.state.seen_outputs.append(output)
        self.state.last_output_hash = self._compute_hash(output)
        self.state.last_timestamp = time.time()

    def check_retry_capacity(self) -> bool:
        return self.state.retry_counter < self.max_retries

    def increment_retry(self) -> None:
        self.state.retry_counter += 1

    def clear_retry_state(self) -> None:
        self.state.retry_counter = 0

    def _compute_hash(self, payload: str) -> str:
        return hashlib.sha256(payload.encode()).hexdigest()

    def process_lifecycle(self, output: str) -> str:
        """Returns one of: BLOCKED_LOOP, RETRY, SYSTEM_ERROR, ACCEPTED."""
        if self.check_loop_condition(output):
            return "BLOCKED_LOOP"
        if not self.evaluate_integrity(output):
            if self.check_retry_capacity():
                self.increment_retry()
                return "RETRY"
            return "SYSTEM_ERROR"
        self.record_state(output)
        self.clear_retry_state()
        return "ACCEPTED"
