"""Pins what governance_loop_guard.PipelineStateEngine does today.

The engine flags a text it has already seen among its last max_history
outputs. These tests record that behaviour as it is, including the two
properties that make a flag a signal and not proof of a stuck model: it
compares across unrelated calls, and a flagged text stays flagged. See the
module docstring. A caller that denies on BLOCKED_LOOP alone will refuse
healthy calls.
"""
from governance_loop_guard import PipelineStateEngine

ROUTINE = "Queue wait is within normal bounds. No action needed."


def test_a_new_text_is_accepted():
    assert PipelineStateEngine().process_lifecycle(ROUTINE) == "ACCEPTED"


def test_a_distinct_text_is_not_flagged():
    engine = PipelineStateEngine()
    engine.process_lifecycle(ROUTINE)
    assert engine.process_lifecycle("Queue wait exceeds bounds. Heal.") == "ACCEPTED"


def test_the_same_verdict_for_a_different_call_is_flagged():
    """The guard cannot tell two healthy calls with the same verdict from a
    stuck model: it sees only the text. This is why a flag is a signal."""
    engine = PipelineStateEngine()
    assert engine.process_lifecycle(ROUTINE) == "ACCEPTED"
    assert engine.process_lifecycle(ROUTINE) == "BLOCKED_LOOP"


def test_a_flagged_text_stays_flagged_and_is_not_recorded_again():
    engine = PipelineStateEngine()
    engine.process_lifecycle(ROUTINE)
    for _ in range(3):
        assert engine.process_lifecycle(ROUTINE) == "BLOCKED_LOOP"
    assert list(engine.state.seen_outputs).count(ROUTINE) == 1


def test_a_flagged_text_is_accepted_again_once_it_leaves_the_window():
    engine = PipelineStateEngine(max_history=3)
    engine.process_lifecycle(ROUTINE)
    for i in range(3):
        engine.process_lifecycle(f"other {i}")
    assert engine.process_lifecycle(ROUTINE) == "ACCEPTED"


def test_a_blank_text_retries_then_becomes_a_system_error():
    engine = PipelineStateEngine(max_retries=2)
    assert engine.process_lifecycle("  ") == "RETRY"
    assert engine.process_lifecycle("") == "RETRY"
    assert engine.process_lifecycle(None) == "SYSTEM_ERROR"


def test_an_accepted_text_clears_the_retry_count():
    engine = PipelineStateEngine(max_retries=1)
    assert engine.process_lifecycle("") == "RETRY"
    assert engine.process_lifecycle(ROUTINE) == "ACCEPTED"
    assert engine.process_lifecycle("") == "RETRY"
