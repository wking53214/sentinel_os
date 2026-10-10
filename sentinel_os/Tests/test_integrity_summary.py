"""Tests for integrity_summary: the part of a DIT verdict that is sealed into
the ledger hash.

The contract under test: only status, the failing gate, and the gate names
are kept. The `failures` strings are dropped, because nothing has confirmed
they never quote rejected prose.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from epistemic.dit_gate import STATUS_BREACH, STATUS_PASS, integrity_summary  # noqa: E402


def test_no_verdict_returns_none():
    assert integrity_summary(None) is None
    assert integrity_summary({}) is None


def test_keeps_status_terminal_gate_and_gate_names():
    verdict = {
        "status": STATUS_BREACH,
        "parity": False,
        "failures": [],
        "terminal_gate": "affective",
        "gates": ["hedging", "affective"],
        "detail": None,
    }
    summary = integrity_summary(verdict)
    assert summary == {
        "status": STATUS_BREACH,
        "terminal_gate": "affective",
        "gates": ["hedging", "affective"],
    }


def test_failure_text_is_never_included():
    secret = "QUOTED-REJECTED-PROSE-DO-NOT-SEAL"
    verdict = {
        "status": STATUS_BREACH,
        "parity": False,
        "failures": [f"affective: {secret}"],
        "terminal_gate": "affective",
        "gates": ["affective"],
        "detail": None,
    }
    summary = integrity_summary(verdict)
    assert secret not in repr(summary)
    assert set(summary.keys()) == {"status", "terminal_gate", "gates"}


def test_gate_list_is_copied_not_shared():
    gates = ["hedging"]
    summary = integrity_summary(
        {"status": STATUS_PASS, "terminal_gate": None, "gates": gates})
    summary["gates"].append("mutated")
    assert gates == ["hedging"]


def test_missing_optional_keys_default_safely():
    summary = integrity_summary({"status": STATUS_PASS})
    assert summary == {"status": STATUS_PASS, "terminal_gate": None, "gates": []}
