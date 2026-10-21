"""Pins the per-trace path of governance_loop_guard.PipelineStateEngine.

Calls that pass a trace_id are judged only against that trace's own outputs.
Calls with no trace_id keep the shared history pinned in
test_governance_loop_guard.py.
"""
from governance_loop_guard import PipelineStateEngine

ROUTINE = "Queue wait is within normal bounds. No action needed."


def test_the_same_text_in_two_traces_is_not_flagged_across_traces():
    engine = PipelineStateEngine()
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"
    assert engine.process_lifecycle(ROUTINE, trace_id="b") == "ACCEPTED"


def test_a_repeat_inside_one_trace_is_flagged():
    engine = PipelineStateEngine()
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "BLOCKED_LOOP"


def test_other_traces_traffic_does_not_hide_a_repeat():
    engine = PipelineStateEngine(max_history=3)
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"
    for i in range(50):
        engine.process_lifecycle(f"other {i}", trace_id="b")
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "BLOCKED_LOOP"


def test_a_flagged_text_stays_flagged_in_its_trace_and_is_not_recorded_again():
    engine = PipelineStateEngine()
    engine.process_lifecycle(ROUTINE, trace_id="a")
    for _ in range(3):
        assert engine.process_lifecycle(ROUTINE, trace_id="a") == "BLOCKED_LOOP"
    assert list(engine.state.trace_outputs["a"]).count(ROUTINE) == 1


def test_a_trace_window_is_bounded_by_max_history():
    engine = PipelineStateEngine(max_history=3)
    engine.process_lifecycle(ROUTINE, trace_id="a")
    for i in range(3):
        engine.process_lifecycle(f"other {i}", trace_id="a")
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"


def test_the_least_recent_trace_is_dropped_when_max_traces_is_exceeded():
    engine = PipelineStateEngine(max_traces=1)
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"
    assert engine.process_lifecycle("other", trace_id="b") == "ACCEPTED"
    assert list(engine.state.trace_outputs) == ["b"]
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "ACCEPTED"


def test_a_recently_used_trace_is_kept_over_an_older_one():
    engine = PipelineStateEngine(max_traces=2)
    engine.process_lifecycle(ROUTINE, trace_id="a")
    engine.process_lifecycle("other", trace_id="b")
    assert engine.process_lifecycle(ROUTINE, trace_id="a") == "BLOCKED_LOOP"
    engine.process_lifecycle("third", trace_id="c")
    assert list(engine.state.trace_outputs) == ["a", "c"]


def test_a_blank_text_in_a_trace_retries_then_becomes_a_system_error():
    engine = PipelineStateEngine(max_retries=2)
    assert engine.process_lifecycle("  ", trace_id="a") == "RETRY"
    assert engine.process_lifecycle("", trace_id="a") == "RETRY"
    assert engine.process_lifecycle(None, trace_id="a") == "SYSTEM_ERROR"


def test_an_untraced_call_does_not_see_traced_outputs():
    engine = PipelineStateEngine()
    engine.process_lifecycle(ROUTINE, trace_id="a")
    assert engine.process_lifecycle(ROUTINE) == "ACCEPTED"
