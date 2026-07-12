"""
Friction Core -- the one place a wait becomes friction.

Before this module the same question ("is this wait long enough to
count?") was answered by a literal 30 in the production harness, a
`.get(..., 30)` fallback in the cassette harness, a `> 30.0` in the
simulator's observer, and a different 300/120 ladder in the Twilio
ingest heuristic. Four thresholds meant four ways to disagree.

Now every governance path calls compute_friction, and the threshold is
passed in -- sourced from the cassette by the caller. This module holds
the RULE (strict greater-than, one event per breaching wait); it holds
no threshold of its own, by design, so there is no number here for a
future edit to quietly diverge from the cassette.

(Item #7 scope: the Twilio ingest heuristics in
twilio_log_ingestion._count_friction unify with this module when the
ingest path is integrated into the production flow.)
"""

from __future__ import annotations


def compute_friction(duration: float, long_wait_threshold: float) -> int:
    """Return the friction contributed by one wait.

    A wait STRICTLY greater than the threshold is one friction event;
    a wait equal to or below it is none. The threshold is the
    cassette's long_wait_threshold, passed in by the caller -- this
    function never sources or defaults it.
    """
    if duration > long_wait_threshold:
        return 1
    return 0
