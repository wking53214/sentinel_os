"""
Tests: observe_consolidated.py
================================

Unit + scenario tests for the consolidated OBSERVE clinical engine.
Run: python3 -m pytest test_observe_consolidated.py -v
"""

import unittest
from datetime import datetime, timezone

from observe_consolidated import (
    ObserveClinicalEngine,
    VitalsSnapshot,
    RiskAdapters,
    RiskOutput,
    BayesianFusion,
    EscalationPolicy,
    OperationalRegime,
    regime_distribution,
    get_age_group,
    ImmutableAuditLedger,
    ProvisionalStore,
)


def make_vitals(**overrides) -> VitalsSnapshot:
    defaults = dict(
        patient_id="P001",
        timestamp=datetime.now(timezone.utc),
        heart_rate=100,
        oxygen_saturation=97.0,
        respiratory_rate=24,
        temperature=37.0,
        context={"age_months": 24},
    )
    defaults.update(overrides)
    return VitalsSnapshot(**defaults)


# ============================================================
# regime_distribution / get_age_group
# ============================================================

class TestRegimeDistribution(unittest.TestCase):

    def test_low_risk_favors_stable(self):
        d = regime_distribution(0.1)
        self.assertEqual(max(d, key=d.get), "stable")

    def test_high_risk_favors_critical(self):
        d = regime_distribution(0.9)
        self.assertEqual(max(d, key=d.get), "critical")
        self.assertGreaterEqual(d["critical"], 0.5)

    def test_mid_risk_favors_warning(self):
        d = regime_distribution(0.55)
        self.assertEqual(max(d, key=d.get), "warning")

    def test_critical_floor_enforced(self):
        # Even at risk=0.0, regime_distribution should respect critical_floor
        d = regime_distribution(0.0, critical_floor=0.05)
        self.assertGreaterEqual(d["critical"], 0.05)

    def test_distribution_sums_to_one(self):
        for score in [0.0, 0.1, 0.3, 0.5, 0.75, 1.0]:
            d = regime_distribution(score)
            self.assertAlmostEqual(sum(d.values()), 1.0, places=5)


class TestAgeGroup(unittest.TestCase):

    def test_neonatal(self):
        self.assertEqual(get_age_group(1), "neonatal")

    def test_infant(self):
        self.assertEqual(get_age_group(6), "infant")

    def test_toddler(self):
        self.assertEqual(get_age_group(24), "toddler")

    def test_child(self):
        self.assertEqual(get_age_group(96), "child")

    def test_missing_age_returns_generic(self):
        self.assertEqual(get_age_group(None), "generic")


# ============================================================
# HEURISTIC ADAPTER
# ============================================================

class TestHeuristicAdapter(unittest.TestCase):

    def test_normal_vitals_low_risk(self):
        out = RiskAdapters.heuristic(make_vitals())
        self.assertLess(out.risk_score, 0.2)
        self.assertEqual(out.confidence, 0.85)  # age present, no history

    def test_critical_o2_triggers(self):
        out = RiskAdapters.heuristic(make_vitals(oxygen_saturation=85.0))
        self.assertGreaterEqual(out.risk_score, 0.5)
        self.assertTrue(any("CRITICAL_O2" in r for r in out.triggered_rules))

    def test_confidence_full_data(self):
        v = make_vitals(context={"age_months": 24, "previous_o2": 96, "previous_hr": 100})
        out = RiskAdapters.heuristic(v)
        self.assertEqual(out.confidence, 0.95)

    def test_confidence_missing_age(self):
        v = make_vitals(context={})
        out = RiskAdapters.heuristic(v)
        self.assertEqual(out.confidence, 0.70)

    def test_age_adjusted_thresholds_differ(self):
        # HR=145 is normal for neonatal (hr_high=160) but tachycardic for child (hr_high=130)
        neonatal = RiskAdapters.heuristic(make_vitals(heart_rate=145, context={"age_months": 1}))
        child = RiskAdapters.heuristic(make_vitals(heart_rate=145, context={"age_months": 96}))
        self.assertFalse(any("TACHYCARDIA" in r for r in neonatal.triggered_rules))
        self.assertTrue(any("TACHYCARDIA" in r for r in child.triggered_rules))


# ============================================================
# BAYESIAN ADAPTER
# ============================================================

class TestBayesianAdapter(unittest.TestCase):

    def test_normal_vitals_low_score(self):
        out = RiskAdapters.bayesian(make_vitals())
        self.assertLess(out.risk_score, 0.3)

    def test_debug_info_populated_not_in_triggered_rules(self):
        out = RiskAdapters.bayesian(make_vitals())
        self.assertIn("z_o2", out.debug_info)
        self.assertIn("z_hr", out.debug_info)
        # Diagnostic floats should NOT appear in triggered_rules text
        for r in out.triggered_rules:
            self.assertNotIn("z_o2", r)

    def test_low_o2_triggers_deviation(self):
        out = RiskAdapters.bayesian(make_vitals(oxygen_saturation=85.0))
        self.assertTrue(any("O2_DEVIATION" in r for r in out.triggered_rules))
        self.assertGreater(out.risk_score, 0.0)

    def test_continuous_likelihood_not_step(self):
        # Two different severities of O2 deviation should produce DIFFERENT
        # risk contributions (not a step function that saturates immediately)
        mild = RiskAdapters.bayesian(make_vitals(oxygen_saturation=88.0))
        severe = RiskAdapters.bayesian(make_vitals(oxygen_saturation=80.0))
        self.assertNotEqual(mild.risk_score, severe.risk_score)
        self.assertGreater(severe.risk_score, mild.risk_score)


# ============================================================
# TRAJECTORY ADAPTER
# ============================================================

class TestTrajectoryAdapter(unittest.TestCase):

    def test_no_history_low_confidence_stable(self):
        out = RiskAdapters.trajectory(make_vitals(context={"age_months": 24}))
        self.assertEqual(out.risk_score, 0.0)
        self.assertEqual(out.confidence, 0.2)  # FIX: low, not misleading medium
        self.assertEqual(out.regime_classification["stable"], 1.0)

    def test_per_minute_threshold_realistic(self):
        # 5% O2 drop over 60 seconds = 5%/min -> should trigger (threshold is 3%/min)
        v = make_vitals(
            oxygen_saturation=89.0,
            context={"age_months": 24, "previous_o2": 94.0, "time_delta_seconds": 60},
        )
        out = RiskAdapters.trajectory(v)
        self.assertTrue(any("O2_MOMENTUM" in r for r in out.triggered_rules))
        self.assertGreater(out.risk_score, 0.0)

    def test_no_name_error_with_partial_history(self):
        # Only previous_hr provided; previous_o2/rr/temp absent.
        # Must not raise NameError when computing bad_trends.
        v = make_vitals(
            heart_rate=160, context={"age_months": 24, "previous_hr": 100, "time_delta_seconds": 60}
        )
        try:
            out = RiskAdapters.trajectory(v)
        except NameError as e:
            self.fail(f"trajectory_adapter raised NameError: {e}")
        self.assertTrue(any("HR_MOMENTUM" in r for r in out.triggered_rules))

    def test_multi_trend_deterioration(self):
        v = make_vitals(
            oxygen_saturation=88.0, heart_rate=145, respiratory_rate=40,
            context={
                "age_months": 24, "previous_o2": 96.0, "previous_hr": 100,
                "previous_rr": 25, "time_delta_seconds": 60,
            },
        )
        out = RiskAdapters.trajectory(v)
        self.assertTrue(any("MULTI_TREND_DETERIORATION" in r for r in out.triggered_rules))

    def test_regime_distribution_reflects_high_risk(self):
        v = make_vitals(
            oxygen_saturation=85.0, heart_rate=150, respiratory_rate=42,
            context={
                "age_months": 24, "previous_o2": 96.0, "previous_hr": 100,
                "previous_rr": 25, "time_delta_seconds": 60,
            },
        )
        out = RiskAdapters.trajectory(v)
        if out.risk_score >= 0.75:
            self.assertEqual(max(out.regime_classification, key=out.regime_classification.get), "critical")


# ============================================================
# DRIFT ADAPTER
# ============================================================

class TestDriftAdapter(unittest.TestCase):

    def test_no_history_returns_stable(self):
        out = RiskAdapters.drift(make_vitals())
        self.assertEqual(out.risk_score, 0.0)
        self.assertEqual(out.regime_classification["stable"], 1.0)

    def test_zero_baseline_not_treated_as_missing(self):
        # FIX verification: baseline_o2=0.0 is a real (if absurd) value, not "missing".
        # The adapter should NOT early-exit just because the value is falsy.
        v = make_vitals(context={
            "age_months": 24,
            "baseline_o2": 0.0,  # falsy but not None
            "history_o2": [95, 96, 94, 95, 96, 95],
        })
        out = RiskAdapters.drift(v)
        # Should proceed to drift calculation, not bail with "insufficient history"
        self.assertNotIn("Insufficient history for drift detection", out.triggered_rules)

    def test_drift_detected_with_shifted_baseline(self):
        v = make_vitals(context={
            "age_months": 24,
            "baseline_o2": 90.0,
            "history_o2": [96, 97, 96, 95, 97, 96, 96],  # mean ~96.1, std small
        })
        out = RiskAdapters.drift(v)
        self.assertTrue(any("O2_DRIFT" in r for r in out.triggered_rules))

    def test_critical_floor_present(self):
        v = make_vitals(context={
            "age_months": 24,
            "baseline_o2": 90.0,
            "history_o2": [96, 97, 96, 95, 97, 96, 96],
        })
        out = RiskAdapters.drift(v)
        self.assertGreaterEqual(out.regime_classification["critical"], 0.02)


# ============================================================
# BEHAVIORAL VACCINE ADAPTER
# ============================================================

class TestBehavioralAdapter(unittest.TestCase):

    def test_septic_shock_pattern_detected(self):
        v = make_vitals(oxygen_saturation=90.0, heart_rate=145, respiratory_rate=38, temperature=39.0)
        out = RiskAdapters.behavioral_vaccine(v)
        self.assertTrue(any("septic_shock" in r for r in out.triggered_rules))
        self.assertGreater(out.risk_score, 0.5)

    def test_multiple_dangerous_patterns_independent(self):
        # Construct vitals that could trigger BOTH septic_shock AND respiratory_distress
        v = make_vitals(oxygen_saturation=88.0, heart_rate=145, respiratory_rate=48, temperature=39.0)
        out = RiskAdapters.behavioral_vaccine(v)
        dangerous_hits = [r for r in out.triggered_rules if r.startswith("DANGEROUS_PATTERN")]
        self.assertGreaterEqual(len(dangerous_hits), 2)  # both fire, not elif-blocked

    def test_alert_none_excludes_benign_pattern(self):
        # Fever-response vitals, but alert is unspecified (None) -> no benign reduction applied
        v = make_vitals(temperature=39.0, heart_rate=135, respiratory_rate=30, context={"age_months": 24})
        out = RiskAdapters.behavioral_vaccine(v)
        self.assertEqual(out.confidence, 0.75)  # alert unknown -> lower confidence
        self.assertFalse(any("BENIGN_PATTERN" in r for r in out.triggered_rules))

    def test_alert_known_allows_benign_pattern(self):
        v = make_vitals(temperature=39.0, heart_rate=135, respiratory_rate=30, context={"age_months": 24, "alert": "fever_monitored"})
        out = RiskAdapters.behavioral_vaccine(v)
        self.assertEqual(out.confidence, 0.90)
        self.assertTrue(any("fever_response" in r for r in out.triggered_rules))

    def test_dangerous_pattern_suppresses_benign_reduction(self):
        # Even with alert known, if a dangerous pattern fires, benign reduction must not apply
        v = make_vitals(oxygen_saturation=90.0, heart_rate=145, respiratory_rate=38, temperature=39.0,
                         context={"age_months": 24, "alert": "fever_monitored"})
        out = RiskAdapters.behavioral_vaccine(v)
        self.assertTrue(any("DANGEROUS_PATTERNS_ACTIVE" in r for r in out.triggered_rules))
        self.assertFalse(any("BENIGN_PATTERN" in r for r in out.triggered_rules))

    def test_base_risk_computed_independently(self):
        # No upstream "base_risk_score" in context at all — must not KeyError/AttributeError
        v = make_vitals(oxygen_saturation=89.0, heart_rate=160, context={"age_months": 24})
        try:
            out = RiskAdapters.behavioral_vaccine(v)
        except (KeyError, AttributeError) as e:
            self.fail(f"behavioral_vaccine raised {type(e).__name__}: {e}")
        self.assertTrue(any("BASE_RISK" in r for r in out.triggered_rules))


# ============================================================
# ADVERSARIAL ADAPTER
# ============================================================

class TestAdversarialAdapter(unittest.TestCase):

    def test_normal_single_reading_not_flagged(self):
        # FIX verification: a single normal reading must NOT trigger constant-value check
        v = make_vitals(context={"age_months": 24, "recent_o2_readings": [96.0]})
        out = RiskAdapters.adversarial(v)
        self.assertLess(out.risk_score, 0.01)
        self.assertEqual(out.regime_classification["stable"], 1.0)

    def test_streak_of_five_identical_flagged(self):
        v = make_vitals(context={"age_months": 24, "recent_o2_readings": [96.0] * 5})
        out = RiskAdapters.adversarial(v)
        self.assertTrue(any("CONSTANT_VALUE_STREAK" in r for r in out.triggered_rules))

    def test_streak_of_four_identical_not_flagged(self):
        v = make_vitals(context={"age_months": 24, "recent_o2_readings": [96.0] * 4})
        out = RiskAdapters.adversarial(v)
        self.assertFalse(any("CONSTANT_VALUE_STREAK" in r for r in out.triggered_rules))

    def test_variance_calculation_over_window(self):
        readings = [96.0, 96.1, 95.9, 96.0, 96.1, 95.9, 96.0, 96.0, 96.1, 95.9]
        v = make_vitals(context={"age_months": 24, "recent_o2_readings": readings})
        # Should not raise, and variance-based check should evaluate without error
        out = RiskAdapters.adversarial(v)
        self.assertIsNotNone(out)

    def test_low_o2_low_hr_not_flagged_as_adversarial(self):
        # FIX verification: this combination is CLINICAL, not adversarial
        v = make_vitals(oxygen_saturation=85.0, heart_rate=70, context={"age_months": 24})
        out = RiskAdapters.adversarial(v)
        self.assertFalse(any("ADVERSARIAL" in r.upper() and "LOW" in r.upper() for r in out.triggered_rules))

    def test_time_aware_rate_threshold(self):
        # 20% change over 30 seconds = 40%/min -> exceeds 15%/min implausibility threshold
        v = make_vitals(
            oxygen_saturation=75.0,
            context={"age_months": 24, "previous_o2": 95.0, "time_delta_seconds": 30},
        )
        out = RiskAdapters.adversarial(v)
        self.assertTrue(any("IMPLAUSIBLE_RATE" in r for r in out.triggered_rules))

    def test_float_safe_zero_comparison(self):
        v = make_vitals(context={"age_months": 24})
        out = RiskAdapters.adversarial(v)
        self.assertLess(out.risk_score, 0.01)
        # regime should be the float-safe special case, not regime_distribution(0.0)
        self.assertEqual(out.regime_classification, {"stable": 1.0, "caution": 0.0, "warning": 0.0, "critical": 0.0})


# ============================================================
# BAYESIAN FUSION
# ============================================================

class TestBayesianFusion(unittest.TestCase):

    def test_empty_outputs(self):
        risk, entropy, regimes, rationale = BayesianFusion.fuse([])
        self.assertEqual(risk, 0.0)
        self.assertEqual(entropy, 0.0)

    def test_entropy_is_shannon(self):
        # A single output with a known distribution should produce true Shannon entropy
        out = RiskAdapters.heuristic(make_vitals())  # stable-dominant distribution
        risk, entropy, regimes, rationale = BayesianFusion.fuse([out])
        manual_entropy = -sum(p * math.log2(p) for p in regimes.values() if p > 0)
        self.assertAlmostEqual(entropy, manual_entropy, places=6)

    def test_fusion_weights_by_confidence(self):
        v_critical = make_vitals(oxygen_saturation=85.0)
        out_heuristic = RiskAdapters.heuristic(v_critical)
        out_trajectory = RiskAdapters.trajectory(v_critical)  # low confidence (no history)

        risk, _, _, _ = BayesianFusion.fuse([out_heuristic, out_trajectory])
        # Fused risk should be closer to the higher-confidence heuristic output
        self.assertGreater(risk, out_trajectory.risk_score)


import math  # noqa: E402  (used in TestBayesianFusion above)


# ============================================================
# ESCALATION POLICY (dwell + hysteresis)
# ============================================================

class TestEscalationPolicy(unittest.TestCase):

    def test_dwell_requires_consecutive_confirmation(self):
        policy = EscalationPolicy(dwell_threshold=2)
        ts = datetime.now(timezone.utc)

        regime, escalated = policy.evaluate(OperationalRegime.WARNING, ts)
        self.assertEqual(regime, OperationalRegime.STABLE)  # not yet escalated
        self.assertFalse(escalated)

        regime, escalated = policy.evaluate(OperationalRegime.WARNING, ts)
        self.assertEqual(regime, OperationalRegime.WARNING)  # second confirmation
        self.assertTrue(escalated)

    def test_dwell_resets_on_disagreement(self):
        """Pending regime resets when a different new_regime arrives mid-dwell.
        NOTE: 'escalation_required' specifically means 'needs PERCEIVE governance
        gate' (stable/caution -> warning/critical). A CAUTION verdict never sets
        escalation_required=True regardless of dwell -- caution is elevated
        monitoring, not a governance event. So this test uses CAUTION->WARNING
        (a genuine escalation) to verify dwell-reset-on-disagreement."""
        policy = EscalationPolicy(dwell_threshold=2)
        ts = datetime.now(timezone.utc)

        policy.evaluate(OperationalRegime.CAUTION, ts)   # pending=CAUTION, count=1
        policy.evaluate(OperationalRegime.WARNING, ts)   # disagreement -> pending resets to WARNING, count=1
        regime, escalated = policy.evaluate(OperationalRegime.WARNING, ts)  # count=2 -> commit
        self.assertEqual(regime, OperationalRegime.WARNING)
        self.assertTrue(escalated)  # stable/caution -> warning/critical IS an escalation

    def test_escalation_lock_prevents_immediate_re_escalation(self):
        policy = EscalationPolicy(dwell_threshold=1, lock_seconds=300)
        ts = datetime.now(timezone.utc)

        regime, escalated = policy.evaluate(OperationalRegime.WARNING, ts)
        self.assertTrue(escalated)
        self.assertTrue(policy.escalation_locked)

        # Immediately try to escalate further to CRITICAL -> locked, regime unchanged
        regime2, escalated2 = policy.evaluate(OperationalRegime.CRITICAL, ts)
        self.assertEqual(regime2, OperationalRegime.WARNING)
        self.assertFalse(escalated2)


# ============================================================
# AUDIT LEDGER
# ============================================================

class TestAuditLedger(unittest.TestCase):

    def test_chain_integrity_valid_after_appends(self):
        ledger = ImmutableAuditLedger()
        for i in range(5):
            ledger.append(f"P{i}", "clinical_assessment", {"risk_score": 0.1 * i})
        self.assertTrue(ledger.verify_integrity())

    def test_tamper_detection(self):
        ledger = ImmutableAuditLedger()
        ledger.append("P1", "clinical_assessment", {"risk_score": 0.5})
        ledger.append("P2", "clinical_assessment", {"risk_score": 0.6})

        # Tamper with an entry's data after the fact
        ledger.entries[0]["data"]["risk_score"] = 0.99

        self.assertFalse(ledger.verify_integrity())

    def test_query_patient(self):
        ledger = ImmutableAuditLedger()
        ledger.append("P1", "clinical_assessment", {"risk_score": 0.1})
        ledger.append("P2", "clinical_assessment", {"risk_score": 0.2})
        ledger.append("P1", "clinical_assessment", {"risk_score": 0.3})

        p1_entries = ledger.query_patient("P1")
        self.assertEqual(len(p1_entries), 2)


# ============================================================
# PROVISIONAL STORE
# ============================================================

class TestProvisionalStore(unittest.TestCase):

    def test_store_and_get(self):
        store = ProvisionalStore()
        store.store("P1", "job-1", 0.5, "warning")
        entry = store.get("P1")
        self.assertEqual(entry["job_id"], "job-1")
        self.assertTrue(entry["is_provisional"])

    def test_reconcile_success(self):
        store = ProvisionalStore()
        store.store("P1", "job-1", 0.5, "warning")
        ok = store.reconcile("P1", "job-1", {"risk_score": 0.55})
        self.assertTrue(ok)
        self.assertFalse(store.get("P1")["is_provisional"])

    def test_reconcile_unknown_patient_returns_false(self):
        store = ProvisionalStore()
        ok = store.reconcile("UNKNOWN", "job-1", {"risk_score": 0.5})
        self.assertFalse(ok)

    def test_reconcile_job_id_mismatch_returns_false(self):
        store = ProvisionalStore()
        store.store("P1", "job-1", 0.5, "warning")
        ok = store.reconcile("P1", "job-WRONG", {"risk_score": 0.55})
        self.assertFalse(ok)

    def test_export_all_is_deep_copy(self):
        store = ProvisionalStore()
        store.store("P1", "job-1", 0.5, "warning")
        exported = store.export_all()
        exported["P1"]["risk_score"] = 999.0
        # internal state must be unaffected
        self.assertEqual(store.get("P1")["risk_score"], 0.5)

    def test_max_capacity_eviction(self):
        store = ProvisionalStore(max_capacity=3)
        for i in range(5):
            store.store(f"P{i}", f"job-{i}", 0.1, "stable")
        self.assertLessEqual(len(store.provisionals), 3)


# ============================================================
# FULL ENGINE INTEGRATION
# ============================================================

class TestEngineIntegration(unittest.TestCase):

    def test_stable_vitals_full_pipeline(self):
        engine = ObserveClinicalEngine()
        v = make_vitals()
        verdict = engine.evaluate(v)
        self.assertEqual(verdict.regime, OperationalRegime.STABLE)
        self.assertFalse(any("CLINICAL_SAFETY_BYPASS" in r for r in verdict.triggered_rules))

    def test_critical_o2_bypasses_dwell_immediately(self):
        engine = ObserveClinicalEngine()
        v = make_vitals(oxygen_saturation=85.0, heart_rate=155, context={"age_months": 24, "force_heavy": True})
        verdict = engine.evaluate(v)
        # FIX verification: single CRITICAL_O2 reading escalates on FIRST call
        self.assertIn(verdict.regime, (OperationalRegime.WARNING, OperationalRegime.CRITICAL))
        self.assertTrue(any("CLINICAL_SAFETY_BYPASS" in r for r in verdict.triggered_rules))

    def test_audit_hash_present_and_chain_valid(self):
        engine = ObserveClinicalEngine()
        v = make_vitals()
        verdict = engine.evaluate(v)
        self.assertTrue(len(verdict.audit_hash) > 0)
        self.assertTrue(engine.audit_ledger.verify_integrity())

    def test_borderline_case_respects_dwell(self):
        engine = ObserveClinicalEngine()
        # Mild tachycardia only -> heuristic risk < 0.5, no hard-rule bypass
        v = make_vitals(heart_rate=145, oxygen_saturation=93.0, context={"age_months": 24})
        v1 = engine.evaluate(v)
        v2 = engine.evaluate(v)
        v3 = engine.evaluate(v)
        # Should remain stable across repeated low-risk readings (no thrashing)
        self.assertEqual(v1.regime, OperationalRegime.STABLE)
        self.assertEqual(v3.regime, OperationalRegime.STABLE)


class TestPhysiologicalReserveAdapter(unittest.TestCase):
    """The 7th engine: gated, calibrated, abstaining."""

    def test_abstains_without_rich_telemetry(self):
        from observe_consolidated import RiskAdaptersPhysiological
        v = make_vitals(context={"age_months": 24})  # no physio axes
        out = RiskAdaptersPhysiological.physiological_reserve(v)
        self.assertEqual(out.risk_score, 0.0)
        self.assertLessEqual(out.confidence, 0.2)  # low-confidence abstention
        self.assertIn("abstains", out.triggered_rules[0])

    def test_healthy_physiology_low_risk(self):
        from observe_consolidated import RiskAdaptersPhysiological
        v = make_vitals(context={
            "organ_coupling_index": 1.0, "perfusion_index": 1.0, "metabolic_load_index": 1.0,
            "reserve_index": 1.0, "infection_burden_index": 0.0, "phase": "stable",
        })
        out = RiskAdaptersPhysiological.physiological_reserve(v)
        self.assertLess(out.risk_score, 0.2)
        self.assertEqual(max(out.regime_classification, key=out.regime_classification.get), "stable")

    def test_critical_physiology_is_critical_dominant(self):
        """The key fix: critical physiology must NOT read as stable-dominant."""
        from observe_consolidated import RiskAdaptersPhysiological
        v = make_vitals(context={
            "organ_coupling_index": 0.3, "organ_failures": 2, "perfusion_index": 0.2,
            "metabolic_load_index": 0.9, "oxygen_demand_index": 0.9, "reserve_index": 0.15,
            "substrate_level": 0.3, "tissue_viability": 0.4, "energy_ratio": 0.3,
            "infection_burden_index": 0.8, "critical_integrity_events": 2,
            "phase": "decompensation", "decomp_index": 0.8,
        })
        out = RiskAdaptersPhysiological.physiological_reserve(v)
        self.assertGreater(out.risk_score, 0.5)
        # warning+critical should dominate stable (the prototype FAILED this)
        wc = out.regime_classification["warning"] + out.regime_classification["critical"]
        self.assertGreater(wc, out.regime_classification["stable"])

    def test_absent_axes_do_not_dilute(self):
        """A single elevated axis with all others absent should still produce elevated risk,
        NOT be diluted toward zero by treating missing axes as 'perfect health'."""
        from observe_consolidated import RiskAdaptersPhysiological
        v = make_vitals(context={"perfusion_index": 0.1, "metabolic_load_index": 1.0,
                                  "oxygen_demand_index": 1.0})  # only capacity axis present, and it's bad
        out = RiskAdaptersPhysiological.physiological_reserve(v)
        self.assertGreater(out.risk_score, 0.4)  # not diluted to ~0
        self.assertIn("capacity", out.debug_info["axes_present"])
        self.assertEqual(len(out.debug_info["axes_present"]), 1)

    def test_confidence_scales_with_axes_present(self):
        from observe_consolidated import RiskAdaptersPhysiological
        one_axis = make_vitals(context={"perfusion_index": 0.5, "metabolic_load_index": 1.0,
                                         "oxygen_demand_index": 1.0})
        out1 = RiskAdaptersPhysiological.physiological_reserve(one_axis)
        many_axes = make_vitals(context={
            "perfusion_index": 0.5, "metabolic_load_index": 1.0, "oxygen_demand_index": 1.0,
            "organ_coupling_index": 0.5, "reserve_index": 0.5, "infection_burden_index": 0.3,
            "phase": "compensation", "hr_history": [120, 130, 125, 135, 128],
        })
        out_many = RiskAdaptersPhysiological.physiological_reserve(many_axes)
        self.assertLess(out1.confidence, out_many.confidence)

    def test_instability_axis_from_hr_history_alone(self):
        """instability is the one axis available from a standard monitor (HR history)."""
        from observe_consolidated import RiskAdaptersPhysiological
        v = make_vitals(context={"hr_history": [120, 160, 100, 170, 90]})  # high variability
        out = RiskAdaptersPhysiological.physiological_reserve(v)
        self.assertIn("instability", out.debug_info["axes_present"])
        self.assertGreater(out.risk_score, 0.0)


class TestPerPatientIsolation(unittest.TestCase):
    """CRITICAL safety property: one patient's escalation state must not affect another's."""

    def test_no_cross_patient_lock_suppression(self):
        from datetime import timedelta
        engine = ObserveClinicalEngine()
        t0 = datetime.now(timezone.utc)
        # Patient A goes critical (sets A's escalation lock)
        rA = engine.evaluate(make_vitals(patient_id="PA", timestamp=t0,
                                         heart_rate=160, oxygen_saturation=84.0,
                                         context={"age_months": 18, "force_heavy": True}))
        # Patient B goes critical 1s later — must still escalate (separate lock)
        rB = engine.evaluate(make_vitals(patient_id="PB", timestamp=t0 + timedelta(seconds=1),
                                         heart_rate=165, oxygen_saturation=83.0,
                                         context={"age_months": 12, "force_heavy": True}))
        self.assertTrue(rA.escalation_required)
        self.assertTrue(rB.escalation_required)  # would FAIL with shared policy

    def test_same_patient_lock_still_applies(self):
        from datetime import timedelta
        engine = ObserveClinicalEngine()
        t0 = datetime.now(timezone.utc)
        r1 = engine.evaluate(make_vitals(patient_id="PC", timestamp=t0,
                                         heart_rate=160, oxygen_saturation=84.0,
                                         context={"age_months": 18, "force_heavy": True}))
        r2 = engine.evaluate(make_vitals(patient_id="PC", timestamp=t0 + timedelta(seconds=30),
                                         heart_rate=158, oxygen_saturation=85.0,
                                         context={"age_months": 18, "force_heavy": True}))
        self.assertTrue(r1.escalation_required)
        self.assertFalse(r2.escalation_required)  # within 300s lock

    def test_independent_entropy_tracking(self):
        engine = ObserveClinicalEngine()
        engine.evaluate(make_vitals(patient_id="PX", heart_rate=160, oxygen_saturation=84.0,
                                    context={"age_months": 18, "force_heavy": True}))
        # PX should have entropy recorded; a fresh patient PY should not inherit it
        self.assertIn("PX", engine._patient_entropy)
        self.assertNotIn("PY", engine._patient_entropy)

    def test_bounded_eviction_lru(self):
        """Per-patient state is capped; least-recently-assessed patient is evicted."""
        from datetime import timedelta
        engine = ObserveClinicalEngine(max_tracked_patients=3)
        t0 = datetime.now(timezone.utc)
        for i in range(5):
            engine.evaluate(make_vitals(patient_id=f"P{i}", timestamp=t0 + timedelta(seconds=i),
                                        context={"age_months": 24}))
        tracked = list(engine._patient_policies.keys())
        self.assertEqual(len(tracked), 3)
        self.assertEqual(tracked, ["P2", "P3", "P4"])  # P0, P1 evicted
        self.assertNotIn("P0", engine._patient_entropy)
        self.assertNotIn("P1", engine._patient_entropy)

    def test_recently_assessed_patient_survives_eviction(self):
        """Touching a patient marks it MRU so it survives the next eviction."""
        from datetime import timedelta
        engine = ObserveClinicalEngine(max_tracked_patients=3)
        t0 = datetime.now(timezone.utc)
        for i in range(3):
            engine.evaluate(make_vitals(patient_id=f"P{i}", timestamp=t0 + timedelta(seconds=i),
                                        context={"age_months": 24}))
        # Re-assess P0 (oldest) -> becomes most-recently-used
        engine.evaluate(make_vitals(patient_id="P0", timestamp=t0 + timedelta(seconds=10),
                                    context={"age_months": 24}))
        # Add a new patient -> should evict P1 (now the LRU), not P0
        engine.evaluate(make_vitals(patient_id="P_NEW", timestamp=t0 + timedelta(seconds=11),
                                    context={"age_months": 24}))
        tracked = list(engine._patient_policies.keys())
        self.assertIn("P0", tracked)
        self.assertNotIn("P1", tracked)


class TestFusionAbstentionAndSyndrome(unittest.TestCase):
    """Hardening: abstainers excluded from fusion; dangerous syndromes floor the risk."""

    def _ro(self, name, risk, conf, rules=None, abstained=False):
        from observe_consolidated import regime_distribution
        return RiskOutput(name, risk, conf, regime_distribution(risk),
                          rules or [], datetime.now(timezone.utc), abstained=abstained)

    def test_abstainers_excluded_from_fusion(self):
        """Three low-conf abstentions must NOT dilute one real detection."""
        outputs = [
            self._ro("heuristic", 0.4, 0.85),
            self._ro("behavioral", 0.9, 0.75, ["DANGEROUS_PATTERN: septic_shock"]),
            self._ro("trajectory", 0.0, 0.2, ["no data"], abstained=True),
            self._ro("drift", 0.0, 0.3, ["no data"], abstained=True),
            self._ro("physiological_reserve", 0.0, 0.2, ["no data"], abstained=True),
        ]
        risk, entropy, probs, rationale = BayesianFusion.fuse(outputs)
        self.assertIn("abstained", rationale)
        self.assertGreaterEqual(risk, 0.9)  # syndrome floor pins to detected severity

    def test_syndrome_floor_pins_risk(self):
        """A dangerous pattern at 0.9 cannot be averaged below 0.9."""
        outputs = [
            self._ro("heuristic", 0.4, 0.85, ["TACHYCARDIA"]),
            self._ro("bayesian", 0.2, 0.85),
            self._ro("behavioral", 0.9, 0.75, ["DANGEROUS_PATTERN: septic_shock"]),
        ]
        risk, _, probs, rationale = BayesianFusion.fuse(outputs)
        self.assertGreaterEqual(risk, 0.9)
        self.assertIn("syndrome floor", rationale)
        # regime re-derived from floored risk should be critical-dominant
        self.assertEqual(max(probs, key=probs.get), "critical")

    def test_no_syndrome_no_floor(self):
        """Without a dangerous pattern, fusion is plain confidence-weighted average."""
        outputs = [
            self._ro("heuristic", 0.2, 0.85, ["TACHYCARDIA"]),
            self._ro("bayesian", 0.1, 0.85),
        ]
        risk, _, _, rationale = BayesianFusion.fuse(outputs)
        self.assertLess(risk, 0.3)
        self.assertNotIn("syndrome floor", rationale)

    def test_all_abstained_degenerate_fallback(self):
        """If every engine abstains, fuse must not divide by zero or crash."""
        outputs = [
            self._ro("trajectory", 0.0, 0.2, ["no data"], abstained=True),
            self._ro("drift", 0.0, 0.3, ["no data"], abstained=True),
        ]
        risk, entropy, probs, rationale = BayesianFusion.fuse(outputs)
        self.assertEqual(risk, 0.0)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=5)

    def test_septic_shock_bypasses_dwell_on_first_reading(self):
        """End-to-end: septic shock escalates immediately, not after dwell."""
        engine = ObserveClinicalEngine()
        v = make_vitals(heart_rate=152, oxygen_saturation=90.0, respiratory_rate=36,
                        temperature=38.6, context={"age_months": 24, "force_heavy": True})
        verdict = engine.evaluate(v)
        self.assertTrue(verdict.escalation_required)
        self.assertIn(verdict.regime, (OperationalRegime.WARNING, OperationalRegime.CRITICAL))
        self.assertTrue(any("DANGEROUS_PATTERN" in r or "CLINICAL_SAFETY_BYPASS" in r
                            for r in verdict.triggered_rules))


if __name__ == "__main__":
    unittest.main()
