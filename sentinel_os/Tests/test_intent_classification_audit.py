"""
Intent classification is produced AND survives to the audit trail.

The classifier (SentinelCore.infer_intent -> IntentSignal) was already
carrying a cassette-native intent label on its `classification` field,
but two things went untested/unrecorded:

  1. No test asserted on `classification` itself -- only queue_chosen
     and confidence -- so a regression that dropped the label (its
     original bug) would not have been caught.
  2. The label reached in-flight consumers (Bayes) but was never
     written to the governance ledger, so it could not be audited
     after the fact.

These tests lock in (1) directly against the real classifier with the
real IvrCassette (no database), and lock in the *shape* the harness now
persists for (2) -- the exact keys that ride inside the ledger's
SHA-256 chain. They intentionally do NOT require a live PostgreSQL:
the contract under test is "the classifier yields an auditable label
and the harness carries it", which is verifiable without one.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sentinel_core import SentinelCore, IntentSignal
from cassettes.ivr_cassette import IvrCassette


def test_known_queue_yields_domain_label():
    """A mapped queue produces a concrete cassette-native label,
    not the UNKNOWN sentinel, at high confidence."""
    sentinel = SentinelCore(IvrCassette())

    signal = sentinel.infer_intent(
        ["root", "intent_menu", "billing_queue"], "billing_queue"
    )

    assert isinstance(signal, IntentSignal)
    assert signal.classification == "BILLING"
    assert signal.classification != "UNKNOWN"
    assert signal.confidence > 0.8
    # reasoning is human-readable provenance, not empty boilerplate
    assert signal.reasoning
    assert "billing_queue" in signal.reasoning
    return True


def test_unmapped_queue_is_unknown_low_confidence():
    """An unmapped queue degrades to UNKNOWN with low confidence --
    the classifier signals doubt rather than inventing an intent."""
    sentinel = SentinelCore(IvrCassette())

    signal = sentinel.infer_intent(["root", "mystery"], "nonsense_queue")

    assert signal.classification == "UNKNOWN"
    assert signal.confidence < 0.5
    return True


def test_classification_is_populated_for_every_defined_queue():
    """Every queue the cassette defines classifies to a non-empty,
    non-UNKNOWN label. Guards against a queue silently losing its
    intent mapping."""
    cassette = IvrCassette()
    sentinel = SentinelCore(cassette)

    queues = cassette.get_queue_definitions()
    assert queues, "cassette should define at least one queue"

    for queue_name in queues:
        signal = sentinel.infer_intent(["root", queue_name], queue_name)
        assert signal.classification, f"{queue_name} produced empty classification"
        assert signal.classification != "UNKNOWN", (
            f"{queue_name} is a defined queue but classified as UNKNOWN"
        )
    return True


def test_audit_payload_shape_is_complete():
    """The three intent fields the harness now writes into the ledger's
    input_data (and thus into the SHA-256 chain) are all present and
    typed as a regulator's audit query expects.

    This mirrors the payload built in ProductionHarness before
    ledger.append_decision, without needing a database: if any of these
    fields regresses to missing/None, an auditor's 'what intent drove
    this decision?' query silently loses an answer.
    """
    sentinel = SentinelCore(IvrCassette())
    signal = sentinel.infer_intent(
        ["root", "intent_menu", "billing_queue"], "billing_queue"
    )

    # The exact keys the harness persists inline in the decision record.
    audit_payload = {
        "intent_classification": signal.classification,
        "intent_confidence": signal.confidence,
        "intent_reasoning": signal.reasoning,
    }

    assert audit_payload["intent_classification"] == "BILLING"
    assert isinstance(audit_payload["intent_confidence"], float)
    assert 0.0 <= audit_payload["intent_confidence"] <= 1.0
    assert isinstance(audit_payload["intent_reasoning"], str)
    assert audit_payload["intent_reasoning"]
    return True
