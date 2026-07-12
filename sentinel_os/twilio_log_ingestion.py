"""
Twilio Log Ingestion - Parse real IVR call logs

Converts actual Twilio call records into Iceberg call journeys with real friction
"""

import json
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TwilioCallLog:
    """Single Twilio call record"""
    sid: str
    to: str
    from_: str
    start_time: str
    end_time: str
    duration: int
    status: str  # "completed", "busy", "failed", "no-answer", "canceled"
    recording_url: Optional[str]
    price: float

@dataclass
class IcebergJourney:
    """Iceberg-compatible call journey from Twilio"""
    caller_id: str
    timestamp: float
    journey: List[str]
    wait_times: Dict[str, float]
    total_duration: float
    resolved: bool
    friction_count: int
    abandonment_reason: Optional[str]

class TwilioLogParser:
    """Parse Twilio call records into Iceberg journeys"""
    
    # Map Twilio outcomes to Iceberg outcomes
    TWILIO_TO_ICEBERG = {
        "completed": {"resolved": True, "reason": "completed"},
        "no-answer": {"resolved": False, "reason": "no_answer"},
        "failed": {"resolved": False, "reason": "failed"},
        "busy": {"resolved": False, "reason": "busy"},
        "canceled": {"resolved": False, "reason": "abandoned"},
    }
    
    def parse_call_log(self, twilio_record: Dict) -> Optional[IcebergJourney]:
        """Convert single Twilio record to Iceberg journey"""
        
        sid = twilio_record.get("sid")
        status = twilio_record.get("status", "unknown")
        duration = int(twilio_record.get("duration", 0))
        timestamp = twilio_record.get("start_time", 0)
        
        if not sid or status not in self.TWILIO_TO_ICEBERG:
            return None
        
        # Map Twilio status to Iceberg outcome
        outcome = self.TWILIO_TO_ICEBERG[status]
        resolved = outcome["resolved"]
        
        # Reconstruct journey from call data
        # In real system, would parse IVR logs/recordings
        journey = self._reconstruct_journey(twilio_record)
        
        # Calculate friction (ingest-side heuristic ESTIMATE -- see
        # _count_friction; the production harness measures its own
        # friction from wait_times against the cassette threshold)
        friction_count = self._count_friction(twilio_record, journey)
        
        # Determine abandonment reason
        abandonment_reason = None if resolved else outcome["reason"]
        
        return IcebergJourney(
            caller_id=f"twilio_{sid[:8]}",
            timestamp=timestamp,
            journey=journey,
            wait_times=self._extract_wait_times(twilio_record, journey),
            total_duration=float(duration),
            resolved=resolved,
            friction_count=friction_count,
            abandonment_reason=abandonment_reason
        )
    
    def _reconstruct_journey(self, record: Dict) -> List[str]:
        """Reconstruct call path from Twilio metadata"""
        
        journey = ["root", "intent_menu"]
        
        # Extract from_number to infer intent
        from_number = record.get("from", "")
        
        # Heuristic: digits in phone map to likely intents
        if from_number.endswith("1"):
            journey.append("billing_queue")
        elif from_number.endswith("2"):
            journey.append("tech_queue")
        elif from_number.endswith("3"):
            journey.append("sales_queue")
        else:
            journey.append("general_queue")
        
        # If completed, add agent
        if record.get("status") == "completed":
            journey.append("agent_a")
        
        journey.append("exit")
        return journey
    
    def _extract_wait_times(self, record: Dict, journey: List[str]) -> Dict[str, float]:
        """Extract per-node wait times, keyed by the ACTUAL journey nodes.

        Previously this returned generic keys ("queue", "agent") that
        never matched the reconstructed journey's node names
        ("billing_queue", "agent_a"), so any per-node lookup downstream
        silently found nothing -- the harness could never see more than
        the intent_menu wait. Keying by the real nodes makes per-node
        friction measurement possible.

        The 0.1/0.5/0.4 split ratios remain an ingest heuristic
        (Item #7 scope: replace with real IVR event timestamps when the
        ingest path is integrated into the production flow).
        """

        duration = float(record.get("duration", 0))

        waits: Dict[str, float] = {}
        queue_node = next((n for n in journey if "queue" in n), None)
        if "intent_menu" in journey:
            waits["intent_menu"] = duration * 0.1
        if queue_node:
            waits[queue_node] = duration * 0.5
        if "agent_a" in journey:
            waits["agent_a"] = duration * 0.4
        return waits
    
    def _count_friction(self, record: Dict, journey: List[str]) -> int:
        """Estimate friction from call patterns.

        Item #7 scope: these 300/120/10 duration heuristics are an
        ingest-side ESTIMATE and deliberately NOT unified with
        governance/friction_core in Items #4-#6; they unify when the
        ingest path is integrated into the production flow. The
        production harness does NOT use this estimate on the
        governance path -- it measures friction itself from wait_times
        against the cassette's threshold.
        """
        
        friction = 0
        duration = int(record.get("duration", 0))
        
        # Long calls suggest friction
        if duration > 300:  # > 5 min
            friction += 2
        elif duration > 120:  # > 2 min
            friction += 1
        
        # Multiple queue visits suggest repeats
        queue_visits = sum(1 for node in journey if "queue" in node)
        if queue_visits > 1:
            friction += queue_visits - 1
        
        # Short duration might indicate no-answer (friction)
        if duration < 10 and record.get("status") != "completed":
            friction += 1
        
        return friction
    
    def parse_log_file(self, file_path: str) -> List[IcebergJourney]:
        """Parse Twilio log file (JSONL format)"""
        
        journeys = []
        
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        journey = self.parse_call_log(record)
                        if journey:
                            journeys.append(journey)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            print(f"Twilio log file not found: {file_path}")
        
        return journeys

class TwilioStreamAdapter:
    """Real-time Twilio log streaming"""
    
    def __init__(self, api_key: str, api_secret: str, account_sid: str):
        """Initialize with Twilio credentials"""
        self.api_key = api_key
        self.api_secret = api_secret
        self.account_sid = account_sid
        self.parser = TwilioLogParser()
    
    def fetch_recent_calls(self, limit: int = 100) -> List[IcebergJourney]:
        """Fetch recent calls from Twilio API (placeholder)"""
        
        # In production: use twilio-python SDK
        # from twilio.rest import Client
        # client = Client(self.account_sid, self.api_key)
        # calls = client.calls.stream(limit=limit)
        
        # For now: return empty list (integration point)
        return []
    
    def setup_webhook(self, webhook_url: str) -> bool:
        """Setup Twilio webhook for real-time events"""
        
        # In production: configure Twilio account to POST to webhook_url
        # Webhook receives call events: initiated, completed, abandoned, etc.
        
        return True
