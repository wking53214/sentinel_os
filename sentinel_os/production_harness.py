"""
Production Harness - Ties all systems together

Real Twilio → Real Prometheus → Real PostgreSQL → Real Claude → Real Governance
"""

import os
import json
import sys
from typing import Dict, Optional
from datetime import datetime

# Import all production components
from twilio_log_ingestion import TwilioLogParser, TwilioStreamAdapter
from metrics_prometheus import PrometheusMetrics
from governance.ledger_postgres import PostgreSQLLedger
from claude_governance_api import ClaudeGovernanceDecider
from observe_perceive_core import ObserveCore, synthesize_percept, EmotionalState, FrictionEvent
from sentinel_core import SentinelCore
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette
from governance.ledger_postgres import GovernanceDecisionRecord
from governance.friction_core import compute_friction
from queue_staffing_bayes_integration import (
    StaffingCoordinator, BayesianIntentEngine, QueueState
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
        self.twilio_parser = TwilioLogParser()
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

    def process_call(self, twilio_record: Dict) -> Dict:
        """Process one call through complete pipeline"""

        # Read the governing policy fresh, at decision time.
        params = self._params()
        long_wait = params.float_value("long_wait_threshold")
        governance_trigger = params.int_value("governance_trigger")

        # 1. Parse Twilio record
        journey = self.twilio_parser.parse_call_log(twilio_record)
        if not journey:
            return {"error": "Failed to parse call"}

        # 2. Observe friction -- ONE rule (friction_core), ONE threshold
        # (the cassette's), applied to the per-node waits actually
        # measured on this call. This measured friction_count is what
        # governs everything downstream: quality scoring, the metrics
        # drift record, and the governance gate. The parser's own
        # heuristic estimate (journey.friction_count) no longer rides
        # the governance path.
        friction_events = []
        friction_count = 0
        for node in journey.journey:
            node_wait = journey.wait_times.get(node, 0)
            if compute_friction(node_wait, long_wait):
                friction_count += 1
                friction_events.append(
                    FrictionEvent(node=node, type="long_wait", severity=0.5, timestamp=0)
                )
        
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
        
        # 7. Governance gate: the cassette's trigger decides who is
        # governed (inclusive >=), and the measured friction_count is
        # what's tested. governed here means "the governor ran".
        claude_decision = None
        governed = friction_count >= governance_trigger
        if self.claude_decider and governed:
            try:
                claude_decision = self.claude_decider.safety_check(
                    "heal_queue",
                    {
                        "queue": first_queue,
                        "wait_time": journey.total_duration,
                        "friction_count": friction_count
                    }
                )
            except Exception as e:
                print(f"Claude decision failed: {e}")
        
        # 8. Ledger: record the governance DECISION -- approvals and
        # rejections alike, whenever the governor actually ran. The
        # record carries the cassette version and the full policy
        # snapshot that governed it, so every row is traceable to the
        # policy in force. applied_value mirrors previous (advisory:
        # nothing was written), parameter_changed=False.
        if self.ledger and claude_decision is not None:
            try:
                approved = bool(claude_decision.get("safe"))
                self.ledger.append_decision(GovernanceDecisionRecord(
                    action_type="governance_decision",
                    node=first_queue,
                    cassette_version=params.cassette_version,
                    input_data={
                        "caller_id": journey.caller_id,
                        "friction_count": friction_count,
                        "governance_trigger": governance_trigger,
                        "wait_time": journey.total_duration,
                        "quality_tier": quality_score.quality_tier.value,
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
                ))
            except Exception as e:
                print(f"Ledger append failed: {e}")
        
        return {
            "caller_id": journey.caller_id,
            "resolved": journey.resolved,
            "quality": quality_score.quality_tier.value,
            "intent": intent_signal.queue_chosen,
            "emotion_frustration": emotion.frustration,
            "claude_safe": claude_decision.get("safe") if claude_decision else None,
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
    
    print(f"\n[RESULTS]")
    print(f"  Calls processed: {summary['calls_processed']}")
    print(f"  Total calls: {summary['calls_total']}")
    print(f"  Resolved: {summary['calls_resolved']}")
    print(f"  Abandoned: {summary['calls_abandoned']}")
    print(f"  Abandonment rate: {summary['abandonment_rate']*100:.1f}%")
    print(f"  Avg wait: {summary['avg_wait_time']:.1f}s")
    print(f"  Governance actions: {summary['governance_actions']}")
    
    # Export metrics
    print(f"\n[PROMETHEUS METRICS]")
    metrics_text = harness.export_metrics()
    print(metrics_text[:500] + "..." if len(metrics_text) > 500 else metrics_text)
    
    # Verify ledger if connected
    if harness.ledger:
        print(f"\n[LEDGER VERIFICATION]")
        verify = harness.verify_ledger()
        print(f"  Ledger OK: {verify.get('ok')}")
        print(f"  Entries: {verify.get('entries', 0)}")
    
    harness.shutdown()
    
    print("\n" + "="*70)
    print("PRODUCTION HARNESS COMPLETE")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
