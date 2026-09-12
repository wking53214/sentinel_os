"""DIT gate: does the governor's reasoning qualify as evidence?

WHAT THIS GATES, AND WHY IT MATTERS
-----------------------------------
`GovernanceDecisionRecord.reasoning` is inside the ledger's canonical
hash (governance/ledger_postgres.py -- `canonical_entry["reasoning"]`).
That means the governing model's free prose is not a log line. It is a
permanently sealed evidentiary fact about why an automated decision was
allowed to stand, and an auditor reading the chain years later will read
it as one.

The kernel fails closed on a response it cannot PARSE. It has never had
an opinion on what the response SAYS. So this passes, today, and gets
hashed:

    "This might be safe, I believe, though it's hard to say."

That is a refusal wearing an approval's clothes. Nothing downstream can
tell the difference, because by the time it reaches the ledger it is just
a string.

WHAT THE GATE IS
----------------
DIT (the Deterministic Integrity Tower) is the library in this stack
built for exactly this judgement. This module is an adapter, not a
reimplementation: the rules, the gates and the verdict all come from DIT,
so the kernel and DIT cannot drift in what they consider evidence.

THE STACK IS DELIBERATELY NARROWER THAN DIT'S DEFAULT
-----------------------------------------------------
DIT's default stack is three gates, and two of them are wrong for this
job:

  IdentityGate    rejects first person. A governing model writing "I find
                  this unsafe" is speaking normally, and the ledger
                  already records WHICH model spoke (model_identity), so
                  the first person is attributed, not anonymous.
  CausalityGate   requires a causal connective or a number. A correct,
                  terse verdict -- "risk too high, policy violation" --
                  has neither, and flagging it would train readers to
                  ignore the flag.

What remains are the two that bear on whether prose is evidence:

  HedgingGate                 "may", "might", "seems", "likely". A hedge
                              sealed into a hash as a justification is
                              the failure this module exists to catch.
  SemanticContaminationGate   "believe", "feel", "hope". DIT treats these
                              as a terminal breach rather than a retryable
                              defect: a model reporting its feelings about
                              a governance action has left the evidentiary
                              frame entirely.

MODES (default is non-breaking)
-------------------------------
  record    (default) Record the verdict on the decision. Never changes
            safe/unsafe. Turning a passing governance decision into a
            refusal on prose grounds is a policy change, and it is not
            this module's to make silently.
  terminal  Refuse on a terminal breach only -- the unambiguous case.
  strict    Refuse on any epistemic failure, hedging included.

Set SENTINEL_EPISTEMIC_MODE, or pass `mode` explicitly.

WHAT THIS MODULE WILL NOT DO
----------------------------
It does not raise. A verdict on prose must never be the reason a
governance decision fails to be produced, so every error path here --
DIT missing, a malformed rule set, an unexpected exception inside the
library -- returns a verdict of "unavailable" and lets the caller
proceed. Fail-open is correct HERE and only here: this module adds
evidence about a decision, it is not part of the safety gate that
decides. The safety gate's own fail-closed contract is untouched.

The verdict is NOT added to the ledger's canonical form. Adding a hashed
field is a change to a contract shared with the twin's
recompute_current_hash, and it is deliberately out of scope.
governance_harness._write_decision maps named fields only, so the verdict
rides on the returned decision dict and stops there.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# ---- modes -------------------------------------------------------------

MODE_RECORD = "record"
MODE_TERMINAL = "terminal"
MODE_STRICT = "strict"
MODE_ENV_VAR = "SENTINEL_EPISTEMIC_MODE"

_VALID_MODES = (MODE_RECORD, MODE_TERMINAL, MODE_STRICT)

# ---- verdict statuses --------------------------------------------------

STATUS_PASS = "pass"
#: A retryable gate failed (today: hedging).
STATUS_FLAGGED = "flagged"
#: A terminal gate failed (today: affective contamination).
STATUS_BREACH = "breach"
#: DIT is not installed, or the gate could not run. Never a judgement.
STATUS_UNAVAILABLE = "unavailable"
#: There was no model-authored prose to judge.
STATUS_SKIPPED = "skipped"


# ---- DIT import, guarded ----------------------------------------------
#
# sentinel_os must remain importable without DIT. The dependency is pinned
# in requirements.txt, but a working tree, a partial install or an
# environment built before this landed must not lose the ability to make
# governance decisions because an evidence annotation is missing.

try:
    from dit import build_stack, default_rules, evaluate
    from dit.gates import HedgingGate, SemanticContaminationGate

    _IMPORT_ERROR: Optional[str] = None
except ImportError as exc:  # pragma: no cover - exercised by the env, not tests
    build_stack = default_rules = evaluate = None  # type: ignore[assignment]
    HedgingGate = SemanticContaminationGate = None  # type: ignore[assignment]
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


_STACK: Optional[tuple] = None
_STACK_ERROR: Optional[str] = None


def _stack():
    """The evidentiary gate stack, built once.

    Returns None (and records why) if it cannot be built, rather than
    raising into a governance call.
    """
    global _STACK, _STACK_ERROR
    if _STACK is not None or _STACK_ERROR is not None:
        return _STACK
    if _IMPORT_ERROR is not None:
        _STACK_ERROR = _IMPORT_ERROR
        return None
    try:
        _STACK = build_stack(
            default_rules(), (HedgingGate, SemanticContaminationGate)
        )
    except Exception as exc:  # noqa: BLE001 - see module docstring: never raise
        _STACK_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    return _STACK


def _unavailable(detail: Optional[str]) -> Dict[str, Any]:
    return {
        "status": STATUS_UNAVAILABLE,
        "parity": None,
        "failures": [],
        "terminal_gate": None,
        "gates": [],
        "detail": detail,
    }


# ---- public API --------------------------------------------------------


def current_mode(mode: Optional[str] = None) -> str:
    """Resolve the operating mode.

    An unrecognised value in the environment resolves to `record` rather
    than raising: a typo in a deployment variable must not be able to
    silently switch the kernel into refusing decisions, nor crash it.
    """
    resolved = (mode or os.environ.get(MODE_ENV_VAR) or MODE_RECORD).strip().lower()
    return resolved if resolved in _VALID_MODES else MODE_RECORD


def gate_reasoning(reasoning: Optional[str]) -> Dict[str, Any]:
    """Judge model-authored reasoning. Never raises.

    The returned dict is JSON-serializable and safe to attach to a
    decision. It is a verdict about prose, never about safety.
    """
    if not reasoning or not str(reasoning).strip():
        return {
            "status": STATUS_SKIPPED,
            "parity": None,
            "failures": [],
            "terminal_gate": None,
            "gates": [],
            "detail": "no reasoning text",
        }

    stack = _stack()
    if stack is None:
        return _unavailable(_STACK_ERROR)

    try:
        verdict = evaluate(str(reasoning), stack)
    except Exception as exc:  # noqa: BLE001 - see module docstring: never raise
        return _unavailable(f"{type(exc).__name__}: {exc}")

    if verdict.terminal:
        status = STATUS_BREACH
    elif not verdict.passed:
        status = STATUS_FLAGGED
    else:
        status = STATUS_PASS

    failures: List[str] = list(verdict.failures)
    return {
        "status": status,
        "parity": verdict.parity,
        "failures": failures,
        "terminal_gate": verdict.terminal_gate,
        "gates": [gate.name for gate in stack],
        "detail": None,
    }


def should_refuse(verdict: Optional[Dict[str, Any]], mode: Optional[str] = None) -> bool:
    """Does this verdict, under this mode, turn the decision into a refusal?

    False for `unavailable` and `skipped` under every mode: an absent
    judgement is not an adverse one.
    """
    if not verdict:
        return False
    status = verdict.get("status")
    resolved = current_mode(mode)
    if resolved == MODE_STRICT:
        return status in (STATUS_FLAGGED, STATUS_BREACH)
    if resolved == MODE_TERMINAL:
        return status == STATUS_BREACH
    return False


def refusal_reason(verdict: Dict[str, Any]) -> str:
    """The `reasoning` string recorded when an epistemic refusal happens.

    It names the gate and quotes nothing of the rejected prose: the
    rejected text is exactly what must not be sealed into the hash.
    """
    failures = verdict.get("failures") or []
    gate = verdict.get("terminal_gate") or "epistemic"
    return (
        f"epistemic_refusal: governor reasoning failed the {gate} gate "
        f"({len(failures)} finding(s)); prose withheld from the record"
    )
