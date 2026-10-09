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

A REPEAT IS A SIGNAL, NOT PROOF
------------------------------------------------------------------------
BLOCKED_LOOP means only that this exact text is among the last max_history
outputs seen. It does not mean the model is stuck. A healthy governor gives
the same short verdict ("within normal bounds") to many different calls, and
a retry of the same input returns the same wording. Two further properties
follow from the code: the text is compared across every call that shares one
engine, not within one input, and a flagged text is never recorded again, so
it stays flagged until max_history other distinct texts push it out.

So callers should record a BLOCKED_LOOP and watch it, and should not deny a
decision on it alone. Tests/test_governance_loop_guard.py pins this behaviour.

PER-TRACE HISTORY
------------------------------------------------------------------------
With no trace_id, the window is shared by every call on one engine, so a busy
engine fills it with other traces' text and a repeat inside one trace can be
missed or flagged for the wrong reason. Passing trace_id gives each trace its
own window of max_history outputs, so one trace's repeats are judged only
against that trace's own outputs. At most max_traces traces are kept; the
least recently used is dropped, and a dropped trace starts with an empty
window if it returns. The retry counter is still shared by all calls.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class EngineState:
    seen_outputs: Deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    last_output_hash: Optional[str] = None
    last_timestamp: Optional[float] = None
    retry_counter: int = 0
    trace_outputs: Dict[str, Deque[str]] = field(default_factory=OrderedDict)


class PipelineStateEngine:
    """Monitors output history to detect generation loops and manage
    a bounded retry lifecycle.

    Callers that pass a trace_id get a separate, bounded history for that
    trace (see PER-TRACE HISTORY below). Callers that pass no trace_id keep
    the original single shared history."""

    def __init__(self, max_retries: int = 5, max_history: int = 1000,
                 max_traces: int = 1000):
        self.state = EngineState(seen_outputs=deque(maxlen=max_history))
        self.max_retries = max_retries
        self.max_history = max_history
        self.max_traces = max_traces

    def evaluate_integrity(self, output: str) -> bool:
        if not output or not output.strip():
            return False
        return True

    def _trace_window(self, trace_id: str) -> Deque[str]:
        traces = self.state.trace_outputs
        window = traces.get(trace_id)
        if window is None:
            window = deque(maxlen=self.max_history)
            traces[trace_id] = window
            if len(traces) > self.max_traces:
                traces.popitem(last=False)
        else:
            traces.move_to_end(trace_id)
        return window

    def check_loop_condition(self, output: str, trace_id: Optional[str] = None) -> bool:
        if trace_id is None:
            return output in self.state.seen_outputs
        return output in self._trace_window(trace_id)

    def record_state(self, output: str, trace_id: Optional[str] = None) -> None:
        window = self.state.seen_outputs if trace_id is None else self._trace_window(trace_id)
        window.append(output)
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

    def process_lifecycle(self, output: str, trace_id: Optional[str] = None) -> str:
        """Returns one of: BLOCKED_LOOP, RETRY, SYSTEM_ERROR, ACCEPTED."""
        if self.check_loop_condition(output, trace_id):
            return "BLOCKED_LOOP"
        if not self.evaluate_integrity(output):
            if self.check_retry_capacity():
                self.increment_retry()
                return "RETRY"
            return "SYSTEM_ERROR"
        self.record_state(output, trace_id)
        self.clear_retry_state()
        return "ACCEPTED"
