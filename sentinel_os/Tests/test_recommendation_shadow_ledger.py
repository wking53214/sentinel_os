"""recommendation_shadow_run / recommendation_shadow_score as new ledger
record kinds (2026-07-31) -- Item 9 (shadow_run_hash), same contract every
prior optional field already uses. Tested against a real ledger, same
convention test_outcome_chain_records.py / test_ai_cost_ledger.py already
established.
"""

CASSETTE_VERSION = "ivr:standard-ivr:2.0.2"


def test_a_shadow_run_hashes_and_verifies(test_ledger):
    r1 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="billing_queue",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 90.0, "baseline_wait": 40.0},
        recommendation={"should_heal": True, "target_wait": 45.0,
                        "confidence": 0.8})
    assert r1["status"] == "created"
    assert test_ledger.verify_chain()["ok"]


def test_a_shadow_score_references_a_real_shadow_run_and_verifies(test_ledger):
    r1 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="billing_queue",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 90.0, "baseline_wait": 40.0},
        recommendation={"should_heal": True, "target_wait": 45.0,
                        "confidence": 0.8})
    r2 = test_ledger.record_recommendation_shadow_score(
        shadow_run_hash=r1["current_hash"],
        actual={"actual_wait": 50.0},
        score={"predicted": 45.0, "actual": 50.0, "error": 5.0})
    assert r2["status"] == "created"
    assert r2["shadow_run_hash"] == r1["current_hash"]
    assert test_ledger.verify_chain()["ok"]


def test_scoring_a_hash_that_was_never_a_shadow_run_is_refused(test_ledger):
    import pytest
    with pytest.raises(ValueError, match="never actually recommended"):
        test_ledger.record_recommendation_shadow_score(
            shadow_run_hash="not-a-real-hash-" + "0" * 40,
            actual={"actual_wait": 50.0}, score={"error": 5.0})


def test_get_unscored_shadow_runs_excludes_scored_ones(test_ledger):
    r1 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="q-unscored",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 90.0}, recommendation={"should_heal": True})
    r2 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="q-scored",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 91.0}, recommendation={"should_heal": True})
    test_ledger.record_recommendation_shadow_score(
        shadow_run_hash=r2["current_hash"],
        actual={"actual_wait": 50.0}, score={"error": 5.0})

    unscored = test_ledger.get_unscored_shadow_runs()
    hashes = {u["shadow_run_hash"] for u in unscored}
    assert r1["current_hash"] in hashes
    assert r2["current_hash"] not in hashes


def test_get_unscored_shadow_runs_returns_the_real_inputs_and_recommendation(
    test_ledger,
):
    inputs = {"current_wait": 90.0, "baseline_wait": 40.0}
    recommendation = {"should_heal": True, "target_wait": 45.0}
    r1 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="q-detail",
        cassette_version=CASSETTE_VERSION,
        inputs=inputs, recommendation=recommendation)
    unscored = test_ledger.get_unscored_shadow_runs()
    match = [u for u in unscored if u["shadow_run_hash"] == r1["current_hash"]]
    assert len(match) == 1
    assert match[0]["inputs"] == inputs
    assert match[0]["recommendation"] == recommendation
    assert match[0]["recommendation_kind"] == "healing_bounds"
    assert match[0]["subject"] == "q-detail"


def test_changing_the_score_changes_the_hash(test_ledger):
    """Tamper-evidence: two different scores for two different shadow
    runs must not collide."""
    r1 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="q-a",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 90.0}, recommendation={"should_heal": True})
    r2 = test_ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject="q-b",
        cassette_version=CASSETTE_VERSION,
        inputs={"current_wait": 91.0}, recommendation={"should_heal": True})
    s1 = test_ledger.record_recommendation_shadow_score(
        shadow_run_hash=r1["current_hash"],
        actual={"actual_wait": 50.0}, score={"error": 5.0})
    s2 = test_ledger.record_recommendation_shadow_score(
        shadow_run_hash=r2["current_hash"],
        actual={"actual_wait": 99.0}, score={"error": 8.0})
    assert s1["current_hash"] != s2["current_hash"]
    assert test_ledger.verify_chain()["ok"]
