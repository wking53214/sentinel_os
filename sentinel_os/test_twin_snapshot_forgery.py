"""H4 -- cassette-snapshot forgery detection (DAP-6.2).

The raw cassette_snapshot body is NOT hashed into current_hash (only its
cassette_hash digest is -- see canonical_fields.OPTIONAL_HASHED_FIELDS). So an
attacker who edits the snapshot body on the primary while leaving cassette_hash
and current_hash intact leaves the hash chain valid: every clear-hash and
payload-recompute check still passes. The twin closes this by cross-checking the
live primary snapshot against the honest copy witnessed in the replica envelope.

No mocks: real seal/open envelope crypto, real writer-consistent current_hash.
"""
import json

import twin_custody as tc
import twin_detector as td
from cassette_forensics import compute_cassette_hash

REPLICA_ID = "cust-witness-01"

HONEST_SNAPSHOT = {
    "cassette_id": "ivr_collections_v3",
    "escalation_threshold": 0.80,
    "max_retries": 3,
    "route_to_human_if_sentiment_below": -0.5,
}


def _honest_row():
    row = {
        "id": 42,
        "record_kind": "governance_decision",
        "action_type": "staffing_adjustment",
        "node": "queue-7",
        "cassette_version": "ivr_collections_v3",
        "input_data": {"queue_depth": 120},
        "policy_parameters": {"target_sla": 30},
        "reason": "queue depth over threshold",
        "decision_output": {"add_agents": 2},
        "previous_value": 10,
        "applied_value": 12,
        "data": {"parameter_changed": True},
        "previous_hash": "0" * 64,
        "cassette_snapshot": HONEST_SNAPSHOT,
        "cassette_hash": compute_cassette_hash(HONEST_SNAPSHOT),
        "call_sid": "CA_test_0042",
    }
    row["current_hash"] = tc.recompute_current_hash(row)
    return row


def _sealed_replica(row):
    """The replica's witnessed honest copy: a real sealed envelope + a decryptor."""
    priv, pub = tc.generate_recipient_keypair()
    aad = {"replica_id": REPLICA_ID, "primary_id": row["id"],
           "current_hash": row["current_hash"]}
    envelope = tc.seal(json.dumps(row).encode(), pub, aad)
    entry = {
        "primary_id": row["id"], "call_sid": row["call_sid"],
        "previous_hash": row["previous_hash"], "current_hash": row["current_hash"],
        "envelope": envelope,
    }
    return [entry], td.OptionADecryptor(priv)


def _primary_feed(row, snapshot):
    """Shape fetch_primary_feed returns, with a chosen (possibly forged) snapshot."""
    return [{
        "id": row["id"], "call_sid": row["call_sid"],
        "previous_hash": row["previous_hash"], "current_hash": row["current_hash"],
        "t": None, "cassette_snapshot": snapshot,
    }]


def _run(replica_entries, feed, decryptor):
    return td.run_detection(replica_entries, feed, [], sla_seconds=60,
                            decryptor=decryptor, replica_id=REPLICA_ID, now=1_000_000.0)


def test_honest_snapshot_is_clean():
    row = _honest_row()
    replica, dec = _sealed_replica(row)
    res = _run(replica, _primary_feed(row, HONEST_SNAPSHOT), dec)
    assert res["verdict"] == "CLEAN"
    assert res["counts"]["match"] == 1
    assert res["counts"]["diverge"] == 0


def test_snapshot_forgery_with_intact_chain_is_caught():
    row = _honest_row()
    replica, dec = _sealed_replica(row)
    forged = dict(HONEST_SNAPSHOT, escalation_threshold=0.05)
    res = _run(replica, _primary_feed(row, forged), dec)
    assert res["verdict"] == "FINDINGS"
    assert res["counts"]["match"] == 0
    assert res["counts"]["diverge"] == 1
    assert res["diverge"][0]["sub"] == "cassette_snapshot_forgery"


def test_snapshot_forgery_requires_deep_verification():
    """Boundary: with no decryptor the honest body is unreadable, so this class
    is structurally invisible -- honest and forged score identically. This is
    the same limit as every other deep-verify check, documented so it is never
    mistaken for a regression."""
    row = _honest_row()
    replica, _ = _sealed_replica(row)
    forged = dict(HONEST_SNAPSHOT, escalation_threshold=0.05)
    res = _run(replica, _primary_feed(row, forged), decryptor=None)
    assert res["verdict"] == "CLEAN"
    assert res["counts"]["diverge"] == 0
