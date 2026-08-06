"""Fail-closed proof for the kernel's domain-blind governor.

This is the kernel-side half of what test_governor_failclosed.py's
"Gate 1" used to prove through claude_governance_api.ClaudeGovernanceDecider.
That class is IVR's (its other methods take queue names and abandonment
rates) and lives in GSA-815; the fail-closed gate underneath it is the
kernel's, and this file proves it without importing anything telephony.

The anthropic client is stubbed, so nothing here touches the network.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from governance_decider import GovernanceDecider


# ---- stubs: a fake Messages API response, no network -------------------------

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
    """Client stub that returns one text block containing `text`."""
    def _create(*args, **kwargs):
        return _FakeMessage([_FakeBlock(text=text)])
    return _create


def _returns_empty():
    """Client stub that returns a response with no content blocks."""
    def _create(*args, **kwargs):
        return _FakeMessage([])
    return _create


def _raises(exc):
    """Client stub that raises a transport error."""
    def _create(*args, **kwargs):
        raise exc
    return _create


def _decider_with(create_fn):
    d = GovernanceDecider(api_key="sk-fake-not-used")
    d.client.messages.create = create_fn
    return d


# ---- an action and details with no domain in them ----------------------------

ACTION = "apply_governed_change"
DETAILS = {"subject": "node-7", "magnitude": 0.42, "reversible": True}


# ---- the fail-closed contract ------------------------------------------------

def test_no_client_fails_closed():
    """No API key -> no client -> refusal, not an approval."""
    d = GovernanceDecider(api_key=None)
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is False
    assert decision["governed"] is False
    assert decision["model_identity"] is None
    assert decision["cost"] is None


def test_fails_closed_on_bad_json():
    d = _decider_with(_returns("this is not json at all"))
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is False, "parse failure must NOT be safe"
    assert decision["governed"] is False
    assert decision["parse_failed"] is True
    assert decision["model_identity"] is None


def test_fails_closed_on_empty_content():
    d = _decider_with(_returns_empty())
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is False
    assert decision["governed"] is False
    assert decision["model_identity"] is None


def test_fails_closed_on_nonbool_safe():
    """'safe' that isn't a bool is unintelligible for a gate."""
    d = _decider_with(_returns(json.dumps({"safe": "yes", "reasoning": "x"})))
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is False
    assert decision["governed"] is False
    assert decision["parse_failed"] is True


def test_fails_closed_on_transport_error():
    d = _decider_with(_raises(RuntimeError("boom")))
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is False
    assert "transport_error" in decision["reasoning"]
    assert decision["model_identity"] is None


def test_approves_valid_safe_true():
    payload = {
        "safe": True, "risk_level": "low", "reasoning": "reversible, in-bounds",
        "recommendations": [], "confidence": 0.9,
    }
    d = _decider_with(_returns(json.dumps(payload)))
    decision = d.safety_check(ACTION, DETAILS)
    assert decision["safe"] is True
    assert decision["governed"] is True
    assert decision["parse_failed"] is False
    assert decision["model_identity"] == "claude-opus-4-6"


def test_approved_decisions_are_logged_refusals_are_not():
    """The log holds decisions that actually came from a model."""
    payload = {"safe": True, "risk_level": "low", "reasoning": "ok",
               "recommendations": [], "confidence": 0.9}
    d = _decider_with(_returns(json.dumps(payload)))
    d.safety_check(ACTION, DETAILS)
    assert len(d.get_decision_log()) == 1

    d.client.messages.create = _returns("garbage")
    d.safety_check(ACTION, DETAILS)
    assert len(d.get_decision_log()) == 1, "a refusal is not a model decision"


def test_system_instruction_names_no_domain():
    """The framing is fixed and domain-blind by design -- if someone
    reintroduces a domain word here, the audit story changes and this
    test is the place that says so."""
    text = GovernanceDecider.SYSTEM_INSTRUCTION.lower()
    for word in ("ivr", "call", "queue", "caller", "agent", "telephony",
                 "mortgage", "loan", "bank"):
        assert word not in text, f"domain word '{word}' in system instruction"
