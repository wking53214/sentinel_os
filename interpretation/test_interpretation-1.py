"""
Proof suite for the interpretation package.

These tests target the GOVERNANCE properties, not the plumbing. Each one
corresponds to a way the subsystem could quietly stop being trustworthy:

  - an unapproved scenario running
  - an approved scenario being edited after the fact
  - a model's opinion becoming the graded answer
  - refusals inflating or deflating the score
  - an empty zone reporting perfect health
  - a sealed annual record being altered after sign-off
  - multi-year slippage hiding inside acceptable single years
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from interpretation import (
    Approver,
    DriftAnalyzer,
    InterpretationContext,
    RealignmentRecord,
    RealignmentTrail,
    Scenario,
    ScenarioGenerator,
    ScenarioIntegrityError,
    ScenarioLibrary,
    StubModelClient,
    TestHarness,
    ToleranceConfig,
    VersionChange,
    ZoneTolerance,
    annual_realignment_report,
    calibration_suggestion,
    monthly_drift_report,
)
from interpretation.drift import STATE_BREACH, STATE_OK, STATE_UNKNOWN, STATE_WATCH
from interpretation.harness import (
    RESULT_ERROR,
    RESULT_INDETERMINATE,
    RESULT_MATCH,
    RESULT_MISMATCH,
)

REG = "TEST-FAIR-LENDING"
ZONES = ["proxy_correlation", "geographic_scope"]


def _scenario(zone="proxy_correlation", options=None, **kwargs):
    return Scenario(
        regulation_id=REG,
        zone=zone,
        question=kwargs.pop("question", "Is this a proxy?"),
        situation=kwargs.pop("situation", {"applicant_zip": "60601", "dti": 0.42}),
        options=options or ["A", "B"],
        **kwargs,
    )


def _approved(zone="proxy_correlation", expected="A", **kwargs):
    s = _scenario(zone=zone, **kwargs)
    s.approve(expected=expected, approver="counsel@example", rationale="strict reading")
    return s


# ---------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------

def test_proposed_scenario_is_not_runnable():
    """A generated scenario cannot be graded against until a human
    approves it. This is the whole containment story for AI involvement."""
    s = _scenario()
    assert s.status == "PROPOSED"
    assert s.expected is None
    assert not s.is_runnable


def test_approval_requires_an_identity_and_a_real_option():
    s = _scenario()
    with pytest.raises(ValueError):
        s.approve(expected="Z", approver="counsel", rationale="r")
    with pytest.raises(ValueError):
        s.approve(expected="A", approver="   ", rationale="r")


def test_approval_records_who_and_when():
    s = _approved()
    assert s.status == "APPROVED"
    assert s.expected == "A"
    assert s.approved_by == "counsel@example"
    assert s.approved_at is not None
    assert s.is_runnable


def test_rejected_scenarios_are_kept_not_deleted():
    """The record of what was considered and declined is itself evidence."""
    lib = ScenarioLibrary()
    s = lib.add(_scenario())
    s.reject(approver="counsel", reason="facts are legally incoherent")
    assert len(lib.all()) == 1
    assert lib.get(s.scenario_id).rejected_reason == "facts are legally incoherent"
    assert not s.is_runnable


def test_double_approval_is_refused():
    s = _approved()
    with pytest.raises(ValueError):
        s.approve(expected="B", approver="other-counsel", rationale="reconsidered")


# ---------------------------------------------------------------------
# Hash binding
# ---------------------------------------------------------------------

def test_editing_an_approved_scenario_voids_it():
    """Approval covers exact facts and an exact answer. Change either and
    the approval no longer means anything."""
    s = _approved()
    s.verify_hash()  # clean

    s.situation["dti"] = 0.99
    with pytest.raises(ScenarioIntegrityError):
        s.verify_hash()


def test_silently_changing_the_expected_answer_is_caught():
    s = _approved(expected="A")
    s.expected = "B"
    with pytest.raises(ScenarioIntegrityError):
        s.verify_hash()


def test_harness_records_tampering_as_error_not_pass():
    lib = ScenarioLibrary()
    good = lib.add(_approved())
    tampered = lib.add(_approved(question="second"))
    tampered.situation["dti"] = 0.01  # post-approval edit

    run = TestHarness(lib).run(lambda s: "A", REG, "v1")

    by_id = {r.scenario_id: r for r in run.results}
    assert by_id[good.scenario_id].result == RESULT_MATCH
    assert by_id[tampered.scenario_id].result == RESULT_ERROR
    assert run.errored == 1


# ---------------------------------------------------------------------
# Generation: AI proposes, never decides
# ---------------------------------------------------------------------

def _stub_response(rows):
    return json.dumps({"scenarios": rows})


def test_generated_scenarios_land_unapproved_with_no_expected_answer():
    client = StubModelClient(_stub_response([
        {"zone": "proxy_correlation", "question": "q1",
         "situation": {"x": 1}, "options": ["A", "B"],
         "model_suggested_answer": "A", "model_reasoning": "because"},
    ]))
    ctx = InterpretationContext(
        regulation_id=REG, regulation_text="text",
        chosen_interpretation="strict", ambiguity_zones=ZONES,
    )
    lib = ScenarioLibrary()
    created = ScenarioGenerator(client).generate(ctx, count=1, library=lib)

    assert len(created) == 1
    s = created[0]
    assert s.status == "PROPOSED"
    assert s.expected is None, "a model suggestion must never become the graded answer"
    assert s.situation["_model_suggested_answer"] == "A"
    assert not s.is_runnable


def test_generator_drops_undeclared_zones_with_a_reason():
    """A model inventing its own zone means the declared zone list is
    incomplete. That is a human conversation, not a rounding error."""
    client = StubModelClient(_stub_response([
        {"zone": "proxy_correlation", "question": "ok", "situation": {}, "options": ["A", "B"]},
        {"zone": "invented_zone", "question": "bad", "situation": {}, "options": ["A", "B"]},
        {"zone": "geographic_scope", "question": "too few", "situation": {}, "options": ["A"]},
    ]))
    ctx = InterpretationContext(
        regulation_id=REG, regulation_text="t",
        chosen_interpretation="strict", ambiguity_zones=ZONES,
    )
    gen = ScenarioGenerator(client)
    created = gen.generate(ctx, count=3, library=ScenarioLibrary())

    assert len(created) == 1
    assert len(gen.rejected) == 2
    reasons = " ".join(r[1] for r in gen.rejected)
    assert "undeclared zone" in reasons
    assert "2+ distinct options" in reasons


def test_prompt_carries_prior_questions_to_avoid_duplicates():
    lib = ScenarioLibrary()
    lib.add(_approved(question="already asked this"))
    client = StubModelClient(_stub_response([]))
    ctx = InterpretationContext(
        regulation_id=REG, regulation_text="t",
        chosen_interpretation="strict", ambiguity_zones=ZONES,
    )
    ScenarioGenerator(client).generate(ctx, count=5, library=lib)
    assert "already asked this" in client.prompts[0]


def test_unparseable_model_output_raises_rather_than_returning_nothing():
    client = StubModelClient("I'm sorry, I can't do that.")
    ctx = InterpretationContext(
        regulation_id=REG, regulation_text="t",
        chosen_interpretation="strict", ambiguity_zones=ZONES,
    )
    with pytest.raises(ValueError):
        ScenarioGenerator(client).generate(ctx, count=1)


# ---------------------------------------------------------------------
# Honest counting
# ---------------------------------------------------------------------

def test_refusal_is_neither_a_pass_nor_a_fail():
    lib = ScenarioLibrary()
    lib.add(_approved(question="a"))
    lib.add(_approved(question="b"))
    lib.add(_approved(question="c"))

    # Sentinel declines everything.
    run = TestHarness(lib).run(lambda s: None, REG, "v1")

    assert run.indeterminate == 3
    assert run.matched == 0
    assert run.mismatched == 0
    assert run.decided == 0
    assert run.alignment is None, "zero evidence must not read as agreement"


def test_alignment_measured_against_decided_not_total():
    lib = ScenarioLibrary()
    lib.add(_approved(question="a", expected="A"))
    lib.add(_approved(question="b", expected="A"))
    lib.add(_approved(question="c", expected="A"))
    lib.add(_approved(question="d", expected="A"))

    answers = iter(["A", "A", "B", None])
    run = TestHarness(lib).run(lambda s: next(answers), REG, "v1")

    assert run.matched == 2
    assert run.mismatched == 1
    assert run.indeterminate == 1
    assert run.decided == 3
    assert run.alignment == pytest.approx(2 / 3)


def test_unapproved_scenarios_are_reported_as_skipped_not_dropped():
    """A shrinking denominator is the easiest way to make drift vanish."""
    lib = ScenarioLibrary()
    lib.add(_approved(question="approved"))
    lib.add(_scenario(question="still pending"))

    run = TestHarness(lib).run(lambda s: "A", REG, "v1")

    assert run.total == 1
    assert len(run.skipped) == 1
    assert "not runnable" in run.skipped[0]["reason"]


def test_resolver_exception_is_recorded_as_evidence_not_a_crash():
    lib = ScenarioLibrary()
    lib.add(_approved())

    def exploding(_s):
        raise RuntimeError("cassette unavailable")

    run = TestHarness(lib).run(exploding, REG, "v1")
    assert run.errored == 1
    assert "cassette unavailable" in run.results[0].detail


def test_answer_outside_the_offered_options_is_an_error():
    lib = ScenarioLibrary()
    lib.add(_approved(options=["A", "B"]))
    run = TestHarness(lib).run(lambda s: "Q", REG, "v1")
    assert run.results[0].result == RESULT_ERROR


# ---------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------

def _run_with(answers_by_zone):
    lib = ScenarioLibrary()
    expected_map = {}
    for zone, answers in answers_by_zone.items():
        for i, _ in enumerate(answers):
            s = lib.add(_approved(zone=zone, question=f"{zone}-{i}", expected="A"))
            expected_map[s.scenario_id] = None
    queue = {zone: list(a) for zone, a in answers_by_zone.items()}

    def resolver(scenario):
        return queue[scenario.zone].pop(0)

    return TestHarness(lib).run(resolver, REG, "v1")


def test_strict_mode_flags_a_single_mismatch():
    """Default posture. Year one should produce data, not comfort."""
    run = _run_with({"proxy_correlation": ["A", "A", "A", "B"]})
    report = DriftAnalyzer().analyze(run)

    zone = report.zones[0]
    assert zone.alignment == pytest.approx(0.75)
    assert zone.state == STATE_BREACH
    assert report.risk_level == "HIGH"


def test_calibrated_tolerance_distinguishes_watch_from_breach():
    tol = ToleranceConfig()
    tol.set_zone(ZoneTolerance(
        zone="proxy_correlation", mode="CALIBRATED",
        watch_below=0.95, breach_below=0.80,
        set_by="risk-committee", rationale="12 months observed",
    ))

    watch = DriftAnalyzer(tol).analyze(_run_with({"proxy_correlation": ["A"] * 9 + ["B"]}))
    assert watch.zones[0].state == STATE_WATCH
    assert watch.risk_level == "MEDIUM"

    breach = DriftAnalyzer(tol).analyze(_run_with({"proxy_correlation": ["A"] * 6 + ["B"] * 4}))
    assert breach.zones[0].state == STATE_BREACH
    assert breach.risk_level == "HIGH"


def test_zone_with_no_decided_scenarios_is_unknown_not_healthy():
    """A zone whose scenarios all errored or were declined is broken.
    Reporting it as 100% is the most dangerous number this could produce."""
    run = _run_with({"geographic_scope": [None, None, None]})
    report = DriftAnalyzer().analyze(run)

    zone = report.zones[0]
    assert zone.alignment is None
    assert zone.state == STATE_UNKNOWN
    assert zone.flagged
    assert report.risk_level == "MEDIUM"


def test_new_zone_inherits_strict_default():
    """An unconfigured zone is one nobody has thought about. It should be
    loud, not quietly passing."""
    tol = ToleranceConfig()
    tol.set_zone(ZoneTolerance(zone="proxy_correlation", mode="CALIBRATED",
                               watch_below=0.5, breach_below=0.2))
    report = DriftAnalyzer(tol).analyze(_run_with({"brand_new_zone": ["A", "B"]}))
    assert report.zones[0].state == STATE_BREACH


def test_missing_baseline_gives_none_delta_not_zero():
    report = DriftAnalyzer().analyze(_run_with({"proxy_correlation": ["A", "A"]}))
    assert report.zones[0].delta is None, "an unknown change is not a zero change"

    with_base = DriftAnalyzer().analyze(
        _run_with({"proxy_correlation": ["A", "A"]}),
        baseline={"proxy_correlation": 0.8},
    )
    assert with_base.zones[0].delta == pytest.approx(0.2)


def test_calibration_needs_history_before_it_suggests_anything():
    assert calibration_suggestion([])["__status__"]["ready"] is False

    reports = [
        DriftAnalyzer().analyze(_run_with({"proxy_correlation": ["A"] * 9 + ["B"]}))
        for _ in range(4)
    ]
    suggestion = calibration_suggestion(reports)
    assert suggestion["__status__"]["ready"] is True
    assert suggestion["proxy_correlation"]["observed_min"] == pytest.approx(0.9)


# ---------------------------------------------------------------------
# Realignment record
# ---------------------------------------------------------------------

def _record(**kwargs):
    defaults = dict(
        record_id="REC-2026-01",
        regulation_id=REG,
        interpretation_version="v1",
        realignment_date="2026-01-15",
        activation_date="2026-02-01",
        legal_sign_off="APPROVED",
        decision="KEEP",
        context="We chose the strict reading in 2025.",
        business_rationale="Conservative lending posture.",
        legal_assessment="No agency guidance forces a change.",
        approved_by=[Approver(name="Counsel", title="General Counsel", date="2026-01-15")],
        decision_rationale="Reading holds.",
    )
    defaults.update(kwargs)
    return RealignmentRecord(**defaults)


def test_record_without_an_approver_is_not_a_governance_record():
    with pytest.raises(ValueError):
        _record(approved_by=[])


def test_update_decision_must_say_what_happens_to_prior_decisions():
    with pytest.raises(ValueError):
        _record(decision="UPDATE")

    ok = _record(decision="UPDATE", version_change=VersionChange(
        from_version="v1", to_version="v2", activation_date="2026-03-01",
        reason="agency tightened the rule",
        decisions_affected=1250, affected_date_range="2025-01-01..2026-02-28",
        retroactive_retest="NOT_REQUESTED",
    ))
    assert ok.decision == "UPDATE"


def test_sealed_record_detects_a_changed_decision():
    rec = _record()
    rec.seal()
    assert rec.verify()

    rec.decision = "RETIRE"
    assert not rec.verify(), "altering the decision after sign-off must break the seal"


def test_typo_fix_in_narrative_does_not_void_legal_signoff():
    """The seal covers substance. Prose edits are not substance."""
    rec = _record()
    rec.seal()
    rec.business_rationale = "Conservative lending posture (typo fixed)."
    assert rec.verify()


def test_record_renders_the_four_blocks():
    run = _run_with({"proxy_correlation": ["A", "A", "B"], "geographic_scope": ["A", "A"]})
    rec = _record(drift=DriftAnalyzer().analyze(run))
    rec.seal()
    payload = json.loads(rec.to_json())

    assert set(payload) == {"metadata", "quick_view", "narrative", "structured_data", "audit_trail"}
    assert payload["quick_view"]["decision"] == "KEEP"
    assert payload["quick_view"]["risk_level"] == "HIGH"
    assert "proxy_correlation" in payload["quick_view"]["drift_summary"]
    assert payload["audit_trail"]["hash"] == rec.record_hash


# ---------------------------------------------------------------------
# The Option C migration
# ---------------------------------------------------------------------

def _year(date, alignment_pattern, version="v1", decision="KEEP", **kw):
    run = _run_with({"proxy_correlation": alignment_pattern})
    rec = _record(
        record_id=f"REC-{date[:4]}",
        realignment_date=date,
        interpretation_version=version,
        decision=decision,
        drift=DriftAnalyzer().analyze(run),
        **kw,
    )
    rec.seal()
    return rec


def test_trail_assembles_the_structured_view_without_rewriting_records():
    trail = RealignmentTrail([
        _year("2024-01-15", ["A"] * 10),
        _year("2025-01-15", ["A"] * 9 + ["B"]),
        _year("2026-01-15", ["A"] * 8 + ["B"] * 2),
    ])
    view = trail.to_structured_view(REG)

    assert view["record_count"] == 3
    assert len(view["history"]) == 3
    assert view["integrity"]["unsealed_records"] == []
    assert [h["decision"] for h in view["history"]] == ["KEEP", "KEEP", "KEEP"]


def test_zone_trend_exposes_multi_year_slippage():
    """Each year can sit inside tolerance while the five-year line slopes
    down. Catching that is the entire reason the annual meeting exists."""
    trail = RealignmentTrail([
        _year("2022-01-15", ["A"] * 10),
        _year("2023-01-15", ["A"] * 10),
        _year("2024-01-15", ["A"] * 9 + ["B"]),
        _year("2025-01-15", ["A"] * 9 + ["B"]),
        _year("2026-01-15", ["A"] * 8 + ["B"] * 2),
    ])
    trend = trail.zone_trend(REG, "proxy_correlation")

    assert len(trend) == 5
    assert trend[0]["alignment"] == pytest.approx(1.0)
    assert trend[-1]["alignment"] == pytest.approx(0.8)
    assert trend[-1]["alignment"] < trend[0]["alignment"]


def test_trail_flags_a_record_altered_after_sealing():
    good = _year("2024-01-15", ["A"] * 10)
    tampered = _year("2025-01-15", ["A"] * 10)
    tampered.decision = "RETIRE"

    trail = RealignmentTrail([good, tampered])
    assert trail.unsealed() == [tampered.record_id]
    assert trail.to_structured_view(REG)["integrity"]["unsealed_records"] == [tampered.record_id]


def test_version_changes_are_queryable_across_years():
    change = VersionChange(
        from_version="v1", to_version="v2", activation_date="2025-03-01",
        reason="agency guidance", changes=["added zone_C check"],
        decisions_affected=980, affected_date_range="2024-01-01..2025-02-28",
        retroactive_retest="SCHEDULED", retroactive_scope="back to 2024-01-01",
    )
    trail = RealignmentTrail([
        _year("2024-01-15", ["A"] * 10),
        _year("2025-01-15", ["A"] * 9 + ["B"], version="v2",
              decision="UPDATE", version_change=change),
    ])
    changes = trail.version_changes(REG)
    assert len(changes) == 1
    assert changes[0]["retroactive_retest"] == "SCHEDULED"
    assert changes[0]["decisions_affected"] == 980


# ---------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------

def test_monthly_report_leads_with_problems():
    run = _run_with({"proxy_correlation": ["A", "A", "B"], "geographic_scope": ["A", "A"]})
    text = monthly_drift_report(DriftAnalyzer().analyze(run))

    assert "Needs attention" in text
    assert text.index("proxy_correlation") < text.index("geographic_scope")
    assert "does not assess whether that reading is correct" in text


def test_monthly_report_says_no_data_rather_than_perfect():
    run = _run_with({"geographic_scope": [None, None]})
    text = monthly_drift_report(DriftAnalyzer().analyze(run))
    assert "no data" in text
    assert "This is not a pass" in text


def test_annual_report_contains_decision_evidence_and_approvals():
    run = _run_with({"proxy_correlation": ["A"] * 9 + ["B"]})
    rec = _record(drift=DriftAnalyzer().analyze(run))
    rec.seal()
    text = annual_realignment_report(rec)

    assert "Annual realignment" in text
    assert "General Counsel" in text
    assert rec.record_hash in text
    assert "not a compliance determination" in text


def test_annual_report_surfaces_multi_year_slippage():
    trail = RealignmentTrail([
        _year("2024-01-15", ["A"] * 10),
        _year("2025-01-15", ["A"] * 9 + ["B"]),
    ])
    current = _year("2026-01-15", ["A"] * 7 + ["B"] * 3)
    trail.add(current)

    text = annual_realignment_report(current, trail=trail)
    assert "Multi-year trend" in text
    assert "Zones lower now than at their first recorded realignment" in text


def test_annual_report_warns_when_calibration_is_premature():
    run = _run_with({"proxy_correlation": ["A"] * 10})
    rec = _record(drift=DriftAnalyzer().analyze(run))
    rec.seal()
    text = annual_realignment_report(rec, history=[DriftAnalyzer().analyze(run)])
    assert "Not enough history" in text


# ---------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------

def test_library_survives_save_and_load(tmp_path):
    lib = ScenarioLibrary()
    approved = lib.add(_approved())
    lib.add(_scenario(question="pending"))

    path = str(tmp_path / "library.json")
    lib.save(path)
    reloaded = ScenarioLibrary.load(path)

    assert len(reloaded.all()) == 2
    assert len(reloaded.runnable(REG)) == 1
    assert len(reloaded.pending_approval(REG)) == 1
    reloaded.get(approved.scenario_id).verify_hash()


def test_full_loop_generate_approve_run_analyze_realign():
    """The whole subsystem, end to end, with no network."""
    client = StubModelClient(_stub_response([
        {"zone": "proxy_correlation", "question": f"q{i}", "situation": {"i": i},
         "options": ["A", "B"], "model_suggested_answer": "A"}
        for i in range(4)
    ]))
    ctx = InterpretationContext(
        regulation_id=REG, regulation_text="text",
        chosen_interpretation="strict reading", ambiguity_zones=ZONES,
    )
    lib = ScenarioLibrary()

    # 1. AI proposes.
    proposed = ScenarioGenerator(client).generate(ctx, count=4, library=lib)
    assert all(not s.is_runnable for s in proposed)

    # 2. Legal approves three and rejects one.
    for s in proposed[:3]:
        s.approve(expected="A", approver="counsel@example", rationale="strict")
    proposed[3].reject(approver="counsel@example", reason="facts incoherent")

    # 3. Sentinel answers; one has drifted.
    answers = iter(["A", "A", "B"])
    run = TestHarness(lib).run(lambda s: next(answers), REG, "v1")
    assert run.decided == 3
    assert run.alignment == pytest.approx(2 / 3)
    assert len(run.skipped) == 1

    # 4. Drift is localized to the zone.
    report = DriftAnalyzer().analyze(run)
    assert report.zones[0].zone == "proxy_correlation"
    assert report.risk_level == "HIGH"

    # 5. Humans decide, and the decision is sealed with its evidence.
    rec = _record(decision="REQUIRES_REVIEW", legal_sign_off="REQUIRES_REVIEW", drift=report)
    rec.seal()
    assert rec.verify()
    assert "REQUIRES_REVIEW" in annual_realignment_report(rec)
