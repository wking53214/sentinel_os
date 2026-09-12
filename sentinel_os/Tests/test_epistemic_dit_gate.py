"""The governor's prose is evidence; this proves something checks it.

`reasoning` is inside the ledger's canonical hash (ledger_postgres.py,
`canonical_entry["reasoning"]`), so what the governing model writes is
sealed as the justification for an automated decision. The kernel fails
closed on a response it cannot parse and has never had an opinion on what
a parseable response says.

Nothing here touches the network, a database, or DIT's network-free
gates beyond importing them.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from epistemic import dit_gate
from epistemic.dit_gate import (
    MODE_RECORD,
    MODE_STRICT,
    MODE_TERMINAL,
    STATUS_BREACH,
    STATUS_FLAGGED,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_UNAVAILABLE,
    current_mode,
    gate_reasoning,
    refusal_reason,
    should_refuse,
)
from governance_decider import GovernanceDecider

dit = pytest.importorskip("dit", reason="DIT is an optional evidence annotation")


# ---- stubs: mirror Tests/test_governance_decider.py --------------------------

class _FakeBlock:
    def __init__(self, text=None, block_type="text"):
        self.type = block_type
        if text is not None:
            self.text = text


class _FakeMessage:
    def __init__(self, blocks, model="claude-opus-4-6", usage=None):
        self.content = blocks
        self.model = model
        self.usage = usage


def _returns(text):
    def _create(*args, **kwargs):
        return _FakeMessage([_FakeBlock(text=text)])
    return _create


def _decider(reasoning, mode=None):
    """A decider whose governor returns an approval with `reasoning`."""
    decider = GovernanceDecider.__new__(GovernanceDecider)
    decider.client = type("_C", (), {})()
    decider.client.messages = type("_M", (), {})()
    decider.client.messages.create = _returns(
        json.dumps({
            "safe": True,
            "risk_level": "low",
            "reasoning": reasoning,
            "recommendations": [],
            "confidence": 0.9,
        })
    )
    decider.model = "claude-opus-4-6"
    decider.epistemic_mode = mode
    decider.decisions = []
    return decider


# ---- the gate itself ---------------------------------------------------------

def test_evidentiary_prose_passes():
    verdict = gate_reasoning("Risk exceeds the configured ceiling; policy violation.")
    assert verdict["status"] == STATUS_PASS
    assert verdict["parity"] == 1.0
    assert verdict["failures"] == []


def test_hedged_prose_is_flagged():
    verdict = gate_reasoning("This might be safe, though it is hard to say.")
    assert verdict["status"] == STATUS_FLAGGED
    assert any("hedging" in failure.lower() for failure in verdict["failures"])


def test_affective_prose_is_a_terminal_breach():
    verdict = gate_reasoning("The action is fine, I believe.")
    assert verdict["status"] == STATUS_BREACH
    assert verdict["terminal_gate"] == "semantic_contamination"


def test_terse_verdicts_are_not_flagged():
    """The reason the stack is narrower than DIT's default.

    DIT's CausalityGate requires a causal connective or a number. A
    correct terse verdict has neither, and flagging it would train
    readers to ignore the flag.
    """
    for prose in (
        "Risk too high, policy violation.",
        "Action denied under the standing cassette.",
        "Approved.",
    ):
        assert gate_reasoning(prose)["status"] == STATUS_PASS


def test_first_person_is_not_flagged():
    """Also deliberate: the ledger records model_identity, so a governor
    writing "I find" is attributed, not anonymous."""
    assert gate_reasoning("I find the action exceeds policy.")["status"] == STATUS_PASS


def test_empty_prose_is_skipped_not_judged():
    for empty in (None, "", "   "):
        assert gate_reasoning(empty)["status"] == STATUS_SKIPPED


def test_verdict_is_json_serializable():
    """It rides on a decision dict that other layers serialize."""
    json.dumps(gate_reasoning("This might be safe."))


def test_gate_never_raises(monkeypatch):
    """A verdict on prose must never be why a decision fails to exist."""
    def _explode(*args, **kwargs):
        raise RuntimeError("rule engine exploded")

    monkeypatch.setattr(dit_gate, "evaluate", _explode)
    verdict = gate_reasoning("any prose at all")
    assert verdict["status"] == STATUS_UNAVAILABLE
    assert "rule engine exploded" in verdict["detail"]


def test_missing_dit_is_unavailable_not_fatal(monkeypatch):
    monkeypatch.setattr(dit_gate, "_STACK", None)
    monkeypatch.setattr(dit_gate, "_STACK_ERROR", "ImportError: no module named dit")
    assert gate_reasoning("prose")["status"] == STATUS_UNAVAILABLE


# ---- modes -------------------------------------------------------------------

def test_default_mode_is_record(monkeypatch):
    monkeypatch.delenv(dit_gate.MODE_ENV_VAR, raising=False)
    assert current_mode() == MODE_RECORD


def test_unrecognised_mode_falls_back_to_record(monkeypatch):
    monkeypatch.setenv(dit_gate.MODE_ENV_VAR, "STRCIT")  # typo
    assert current_mode() == MODE_RECORD


def test_mode_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(dit_gate.MODE_ENV_VAR, "strict")
    assert current_mode() == MODE_STRICT


@pytest.mark.parametrize(
    "status_prose,mode,refuses",
    [
        ("This might be safe.", MODE_RECORD, False),
        ("This might be safe.", MODE_TERMINAL, False),
        ("This might be safe.", MODE_STRICT, True),
        ("It is fine, I believe.", MODE_RECORD, False),
        ("It is fine, I believe.", MODE_TERMINAL, True),
        ("It is fine, I believe.", MODE_STRICT, True),
        ("Policy violation.", MODE_STRICT, False),
    ],
)
def test_refusal_matrix(status_prose, mode, refuses):
    assert should_refuse(gate_reasoning(status_prose), mode) is refuses


def test_absent_judgement_is_never_adverse():
    for verdict in (None, {}, {"status": STATUS_UNAVAILABLE}, {"status": STATUS_SKIPPED}):
        for mode in (MODE_RECORD, MODE_TERMINAL, MODE_STRICT):
            assert should_refuse(verdict, mode) is False


def test_refusal_reason_does_not_quote_the_rejected_prose():
    """The rejected text is exactly what must not reach the hash."""
    prose = "It is fine, I believe, and the secret code is hunter2."
    reason = refusal_reason(gate_reasoning(prose))
    assert "hunter2" not in reason
    assert "semantic_contamination" in reason


# ---- the decider ------------------------------------------------------------

def test_decision_carries_the_verdict():
    decision = _decider("Risk exceeds the ceiling.").safety_check("act", {})
    assert decision["safe"] is True
    assert decision["reasoning_integrity"]["status"] == STATUS_PASS


def test_record_mode_does_not_change_the_verdict():
    """Non-breaking by default: turning a passing decision into a refusal
    on prose grounds is a policy change, not this module's to make."""
    decision = _decider("It is fine, I believe.", mode=MODE_RECORD).safety_check("a", {})
    assert decision["safe"] is True
    assert decision["governed"] is True
    assert decision["reasoning_integrity"]["status"] == STATUS_BREACH


def test_strict_mode_refuses_and_fails_closed():
    decision = _decider("This might be safe.", mode=MODE_STRICT).safety_check("a", {})
    assert decision["safe"] is False
    assert decision["governed"] is False
    assert decision["reasoning"].startswith("epistemic_refusal:")


def test_epistemic_refusal_is_not_a_parse_failure():
    """The response parsed. What failed is its claim on being evidence."""
    decision = _decider("This might be safe.", mode=MODE_STRICT).safety_check("a", {})
    assert decision["parse_failed"] is False


def test_epistemic_refusal_keeps_the_real_model_identity():
    """Every other refusal path nulls model_identity because no model
    spoke. Here one did; what is refused is what it said. Nulling a real
    identity would forge a fact in a tamper-evident record."""
    decision = _decider("This might be safe.", mode=MODE_STRICT).safety_check("a", {})
    assert decision["model_identity"] == "claude-opus-4-6"


def test_terminal_mode_passes_a_merely_hedged_approval():
    decision = _decider("This might be safe.", mode=MODE_TERMINAL).safety_check("a", {})
    assert decision["safe"] is True
    assert decision["reasoning_integrity"]["status"] == STATUS_FLAGGED
