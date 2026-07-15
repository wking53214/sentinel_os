"""
Production Harness - Ties all systems together

Real Twilio → Real Prometheus → Real PostgreSQL → Real Claude → Real Governance
"""

import os
from typing import Dict

# Import all production components
from twilio_log_ingestion import TwilioLogParser, TwilioStreamAdapter
from metrics_prometheus import PrometheusMetrics
from governance.ledger_postgres import PostgreSQLLedger
from claude_governance_api import ClaudeGovernanceDecider
from observe_perceive_core import ObserveCore, FrictionEvent
from sentinel_core import SentinelCore
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette
from tracing import tracer
from governance.ledger_postgres import GovernanceDecisionRecord
from governance.friction_core import compute_friction
from queue_staffing_bayes_integration import (
    StaffingCoordinator, BayesianIntentEngine
)

class IcebergProductionHarness:
    """Complete production system: all components wired together"""
    
    def __init__(self, config: Dict, cassette=None):
        """Initialize production system.

        The cassette is THE governing policy for this harness. It is
        loaded (or injected), schema-validated fail-loud, and every
        governance number read later in process_call comes from it --
        never from a literal in this file.
        """

        self.config = config

        # Governing cassette: injected, or loaded for the configured
        # domain. Validated here so an invalid policy halts construction.
        self.cassette = cassette or CassetteLoader().load_cassette(
            config.get("cassette_domain", "ivr")
        )
        validate_cassette(self.cassette)

        # Data sources
        self.twilio_parser = TwilioLogParser(cassette=self.cassette)
        self.twilio_adapter = None  # Will init if API key provided

        # Observability
        self.metrics = PrometheusMetrics()
        self.observer = ObserveCore()
        self.sentinel = SentinelCore(self.cassette)
        
        # Persistence
        self.ledger = None  # Will init if DB config provided
        
        # Governance
        self.claude_decider = None  # Will init if API key provided
        
        # Operations
        self.staffing = StaffingCoordinator()
        self.bayes = BayesianIntentEngine()
        
        self._init_optional_components()
    
    def _init_optional_components(self):
        """Initialize components that require external credentials"""
        
        # PostgreSQL ledger
        if self.config.get("postgres_host"):
            try:
                self.ledger = PostgreSQLLedger(
                    host=self.config.get("postgres_host", "localhost"),
                    port=self.config.get("postgres_port", 5432),
                    dbname=self.config.get("postgres_db", "iceberg"),
                    user=self.config.get("postgres_user", "iceberg"),
                    password=self.config.get("postgres_password", "iceberg")
                )
                print("✓ PostgreSQL ledger connected")
            except Exception as e:
                print(f"⚠ PostgreSQL not available: {e}")
                self.ledger = None
        
        # Claude API
        if self.config.get("claude_api_key"):
            try:
                self.claude_decider = ClaudeGovernanceDecider(
                    api_key=self.config.get("claude_api_key"),
                    governance_params=self._params(),
                )
                print("✓ Claude governance API connected")
            except Exception as e:
                print(f"⚠ Claude API not available: {e}")
                self.claude_decider = None
        
        # Twilio
        if self.config.get("twilio_account_sid"):
            try:
                self.twilio_adapter = TwilioStreamAdapter(
                    api_key=self.config.get("twilio_api_key", ""),
                    api_secret=self.config.get("twilio_api_secret", ""),
                    account_sid=self.config.get("twilio_account_sid", "")
                )
                print("✓ Twilio log adapter connected")
            except Exception as e:
                print(f"⚠ Twilio not available: {e}")
    
    def _params(self):
        """Re-validate and read the cassette AT DECISION TIME, fresh on
        every call. Never cached: if the governing policy is swapped,
        the very next decision must see it -- a cached snapshot would be
        a second, stale source of truth."""
        return validate_cassette(self.cassette)

    def swap_cassette(self, cassette) -> None:
        """Replace the governing cassette (validated fail-loud). The
        next process_call reads the new policy; nothing is cached across
        the swap."""
        validate_cassette(cassette)
        self.cassette = cassette
        self.sentinel = SentinelCore(cassette)
        self.twilio_parser.cassette = cassette

    def process_call(self, twilio_record: Dict) -> Dict:
        """Process one call through complete pipeline"""

        with tracer.start_as_current_span("process_call") as root_span:
            call_sid = twilio_record.get("sid")
            root_span.set_attribute("call.sid", call_sid or "unknown")

            # 0. Reject duplicate submissions (Option A: hard reject).
            if call_sid and self.ledger:
                with tracer.start_as_current_span("dedup_check"):
                    if self.ledger.sid_exists(call_sid):
                        root_span.set_attribute("call.duplicate", True)
                        return {
                            "error": "duplicate_sid",
                            "detail": f"Call {call_sid} has already been processed",
                            "sid": call_sid,
                        }

            # Read the governing policy fresh, at decision time.
            params = self._params()
            long_wait = params.float_value("long_wait_threshold")
            governance_trigger = params.int_value("governance_trigger")

            # 1. Parse Twilio record
            with tracer.start_as_current_span("twilio_parse") as parse_span:
                journey = self.twilio_parser.parse_call_log(twilio_record)
                if not journey:
                    parse_span.set_attribute("parse.success", False)
                    return {"error": "Failed to parse call"}
                parse_span.set_attribute("parse.success", True)
                parse_span.set_attribute("call.duration", journey.total_duration)

            # 2. Observe friction
            friction_events = []
            measured_waits = getattr(journey, "wait_times", {}) or {}
            if measured_waits:
                friction_count = 0
                for node in journey.journey:
                    node_wait = measured_waits.get(node, 0)
                    if compute_friction(node_wait, long_wait):
                        friction_count += 1
                        friction_events.append(
                            FrictionEvent(
                                node=node,
                                type="long_wait",
                                severity=0.5,
                                timestamp=0
                            )
                        )
            else:
                friction_count = getattr(journey, "friction_count", 0)

            # 3. Perceive emotional state
            emotion = self.observer.get_emotional_state(
                journey.caller_id, friction_events, journey.total_duration
            )

            # 4. Sentinel: Infer intent & quality
            first_queue = next((n for n in journey.journey if "queue" in n), "general_queue")
            intent_signal = self.sentinel.infer_intent(journey.journey, first_queue)
            quality_score = self.sentinel.score_outcome_quality(
                journey.resolved, journey.total_duration,
                friction_count, emotion
            )

            # 5. Record metrics
            self.metrics.record_call(
                wait_time=journey.total_duration * 0.3,
                resolved=journey.resolved,
                resolution_time=journey.total_duration
            )

            if friction_count > 0:
                self.metrics.record_drift_detection(first_queue, 0.2)

            # 6. Bayes: Update intent success rates
            self.bayes.observe_outcome(
                intent_signal.queue_chosen,
                journey.resolved,
                journey.total_duration
            )

            # 7. Governance gate
            claude_decision = None

            governance_friction_count = max(
                friction_count,
                getattr(journey, "friction_count", 0) or 0
            )

            governed = governance_friction_count >= governance_trigger
            root_span.set_attribute("call.governed", governed)
            root_span.set_attribute("call.queue", first_queue)
            root_span.set_attribute("call.friction_count", friction_count)

            if self.claude_decider and governed:
                with tracer.start_as_current_span("governance_decision") as gov_span:
                    try:
                        claude_decision = self.claude_decider.safety_check(
                            "heal_queue",
                            {
                                "queue": first_queue,
                                "wait_time": journey.total_duration,
                                "friction_count": friction_count
                            }
                        )
                        gov_span.set_attribute("decision.approved", bool(claude_decision.get("safe")))
                    except Exception as e:
                        print(f"Claude decision failed: {e}")
                        gov_span.record_exception(e)
                        claude_decision = {
                            "safe": False,
                            "governed": False,
                            "parse_failed": True,
                            "reasoning": f"Governor exception: {str(e)}",
                            "confidence": 0.0
                        }

            elif governed:
                claude_decision = {
                    "safe": False,
                    "governed": False,
                    "parse_failed": False,
                    "reasoning": "Governance required but no governor configured",
                    "confidence": 0.0
                }
        
            # 8. Ledger: record the governance DECISION
            if self.ledger and claude_decision is not None:
                with tracer.start_as_current_span("ledger_write"):
                    try:
                        approved = bool(claude_decision.get("safe"))
                        root_span.set_attribute("call.approved", approved)
                        self.ledger.append_decision(GovernanceDecisionRecord(
                            action_type="governance_decision",
                            node=first_queue,
                            cassette_version=params.cassette_version,
                            input_data={
                                "caller_id": journey.caller_id,
                                "call_sid": call_sid,
                                "friction_count": friction_count,
                                "governance_trigger": governance_trigger,
                                "wait_time": journey.total_duration,
                                "quality_tier": quality_score.quality_tier.value,
                                "intent_classification": intent_signal.classification,
                                "intent_confidence": intent_signal.confidence,
                                "intent_reasoning": intent_signal.reasoning,
                            },
                            policy_parameters=params.snapshot(),
                            reasoning=claude_decision.get("reasoning", ""),
                            output={
                                "approved": approved,
                                "risk_level": claude_decision.get("risk_level"),
                                "confidence": claude_decision.get("confidence"),
                            },
                            previous_value=journey.total_duration,
                            applied_value=journey.total_duration,
                            parameter_changed=False,
                        ), governance_params=params)
                    except Exception as e:
                        print(f"Ledger append failed: {e}")
        
            return {
                "caller_id": journey.caller_id,
                "resolved": journey.resolved,
                "quality": quality_score.quality_tier.value,
                "intent": intent_signal.queue_chosen,
                "intent_classification": intent_signal.classification,
                "emotion_frustration": emotion.frustration,
                "claude_safe": claude_decision.get("safe") if claude_decision else None,
                "governance_required": governed,
                "governance_approved": (
                    claude_decision.get("safe", False)
                    if claude_decision is not None
                    else False
                ),
                "governance_blocked": (
                    governed and (
                        claude_decision is None or
                        not claude_decision.get("safe", False)
                    )
                ),
                "metrics_recorded": True,
                "friction_count": friction_count,
                "governed": governed,
                "governance_trigger": governance_trigger,
                "cassette_version": params.cassette_version,
            }
    
    def process_batch(self, twilio_records: list) -> Dict:
        """Process batch of calls through complete pipeline"""
        
        results = []
        for record in twilio_records:
            result = self.process_call(record)
            results.append(result)
        
        summary = self.metrics.get_summary()
        summary["calls_processed"] = len(results)
        summary["results"] = results
        
        return summary
    
    def export_metrics(self) -> str:
        """Export Prometheus metrics"""
        return self.metrics.export_prometheus()
    
    def verify_ledger(self) -> Dict:
        """Verify ledger integrity (if connected)"""
        if not self.ledger:
            return {"error": "Ledger not connected"}
        
        return self.ledger.verify_chain(mode="tolerant")
    
    def shutdown(self):
        """Cleanup resources"""
        if self.ledger:
            self.ledger.close()

def main():
    """Run production harness"""
    
    print("\n" + "="*70)
    print("ICEBERG PRODUCTION HARNESS - END-TO-END INTEGRATION")
    print("="*70)
    
    # Load config from environment
    config = {
        "postgres_host": os.getenv("POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", 5432)),
        "postgres_db": os.getenv("POSTGRES_DB", "iceberg"),
        "postgres_user": os.getenv("POSTGRES_USER", "iceberg"),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", "iceberg"),
        "claude_api_key": os.getenv("CLAUDE_API_KEY"),
        "twilio_account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
        "twilio_api_key": os.getenv("TWILIO_API_KEY"),
        "twilio_api_secret": os.getenv("TWILIO_API_SECRET"),
    }
    
    # Initialize harness
    harness = IcebergProductionHarness(config)
    
    # Simulate batch of calls
    print("\n[BATCH 1] Processing 5 calls through production pipeline...")
    
    mock_calls = [
        {"sid": "CA001", "status": "completed", "duration": 120, "from": "+1111", "to": "+billing"},
        {"sid": "CA002", "status": "completed", "duration": 150, "from": "+2222", "to": "+tech"},
        {"sid": "CA003", "status": "no-answer", "duration": 30, "from": "+1111", "to": "+billing"},
        {"sid": "CA004", "status": "completed", "duration": 200, "from": "+3333", "to": "+sales"},
        {"sid": "CA005", "status": "failed", "duration": 10, "from": "+2222", "to": "+tech"},
    ]
    
    summary = harness.process_batch(mock_calls)
    
    print("\n[RESULTS]")
    print(f"  Calls processed: {summary['calls_processed']}")
    print(f"  Total calls: {summary['calls_total']}")
    print(f"  Resolved: {summary['calls_resolved']}")
    print(f"  Abandoned: {summary['calls_abandoned']}")
    print(f"  Abandonment rate: {summary['abandonment_rate']*100:.1f}%")
    print(f"  Avg wait: {summary['avg_wait_time']:.1f}s")
    print(f"  Governance actions: {summary['governance_actions']}")
    
    # Export metrics
    print("\n[PROMETHEUS METRICS]")
    metrics_text = harness.export_metrics()
    print(metrics_text[:500] + "..." if len(metrics_text) > 500 else metrics_text)
    
    # Verify ledger if connected
    if harness.ledger:
        print("\n[LEDGER VERIFICATION]")
        verify = harness.verify_ledger()
        print(f"  Ledger OK: {verify.get('ok')}")
        print(f"  Entries: {verify.get('entries', 0)}")
    
    harness.shutdown()
    
    print("\n" + "="*70)
    print("PRODUCTION HARNESS COMPLETE")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
