"""Epistemic integrity: gating the governor's own prose.

The kernel already gates what reaches the governor (governor_injection_
defense fences untrusted caller data) and the shape of what comes back
(governance_decider fails closed on anything unparseable). Nothing gates
the CONTENT of the free text the governor writes -- and that text is
sealed into the ledger's canonical hash as the recorded justification for
a governance decision.

This package is that missing gate. It is a thin adapter over DIT, the
Deterministic Integrity Tower, which is the library in this stack whose
whole purpose is deciding whether model prose is allowed to count as
evidence.
"""

from .dit_gate import (
    MODE_ENV_VAR,
    MODE_RECORD,
    MODE_STRICT,
    MODE_TERMINAL,
    current_mode,
    gate_reasoning,
    should_refuse,
)

__all__ = [
    "MODE_ENV_VAR",
    "MODE_RECORD",
    "MODE_STRICT",
    "MODE_TERMINAL",
    "current_mode",
    "gate_reasoning",
    "should_refuse",
]
