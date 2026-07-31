"""
Twilio Log Ingestion - Parse real IVR call logs

Converts actual Twilio call records into Iceberg call journeys with real friction

THE ivr_events INGESTION CONTRACT
----------------------------------
A bare Twilio Call record (sid/from/to/duration/status) carries no per-node
routing data -- there is no field on it that says which queue a caller
reached or how long they waited at each stop. Absent that, this module has
always had to guess: _reconstruct_journey picks a queue from the last digit
of the caller's phone number, and _extract_wait_times slices total call
duration into a fixed 0.1/0.5/0.4 ratio. Both are real, disclosed heuristics
(see their docstrings) and both now travel stamped ESTIMATED (event_v1.py) --
but a stamped guess is still a guess.

Fixing that for real means a genuinely different input: real per-node events
from wherever a caller's actual path is observed -- a Twilio Studio Flow
execution log, TaskRouter Task/Reservation events, a custom IVR application's
own Gather/Enqueue/Dequeue webhooks, or something else entirely. Which of
those a given deployment has is an integration decision, not a code decision,
so this module does not pick one. Instead it accepts a single generic shape
(IVRNodeEvent: node name + wait seconds + a source label) on the incoming
Twilio record, under the optional key "ivr_events". Whatever real system a
deployment wires up is responsible for translating its own events into that
shape; this module does not care which system it was.

If a record carries a valid ivr_events list, the journey and wait times come
from it directly and are stamped VERIFIED, with the method naming the
source label the caller supplied (so the ledger shows which real system
produced it, not just that it was "real"). If ivr_events is absent, nothing
about existing behavior changes: the phone-digit / ratio-split fallback runs
exactly as before, stamped ESTIMATED. If ivr_events is PRESENT but malformed
(missing node/wait_seconds/source, negative wait, empty list, an unrecognized
role), parsing fails loud rather than silently falling back -- a broken
real-data integration that quietly downgraded to guesses would be a worse
failure mode than an exception, the same fail-loud posture _count_friction
already takes on missing cassette thresholds.

NODE ROLES (2026-07-31): each event may optionally declare a `role` --
one of NODE_ROLE_QUEUE, NODE_ROLE_AGENT, NODE_ROLE_ESCALATION -- naming
what kind of stop this is, instead of requiring downstream code to guess
from the node's NAME (see the disclosed limitation this replaces, below).
Partial tagging is fine: an integration can tag only the stops it's sure
about and leave `role` unset on the rest -- each stop is judged on its own,
there is no all-or-nothing requirement across one call's events. A stop
with no role tag falls back to the existing name-convention guess for
THAT stop only. This module never invents a role for an event that didn't
declare one; it also never renames a node to match its role.

DISCLOSED LIMITATION, NOW PARTIALLY CLOSED (2026-07-31): the rest of the
pipeline (SentinelCore.infer_intent/prescribe_queue_reordering, the
production harness's queue detection, ObservePerceiveCore's resolution
detection) used to identify queue/agent stops ONLY by name convention --
a node counted as a queue stop only if "queue" was a substring of its
name, and only literal names in {agent_a, agent_b, agent_c, agent_d, ...}
counted as agent/resolution nodes (see observe_perceive_core.RESOLUTION_NODES).
Those checks now look for a role tag FIRST and fall back to the name
convention only when no tag is present, so a real event source no longer
has to rename its stops to match Sentinel's convention -- it can just
declare `role` on the events it's sure about. What's still true: a stop
with NO role tag is still only recognized by name, so a real source that
tags nothing gets the exact behavior this module always had.
"""

import json
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from cassette_schema import validate_cassette
from event_v1 import PROVENANCE_VERIFIED, PROVENANCE_ESTIMATED

# Named once, here, next to the code that produces the estimate, so the
# label recorded in the ledger and the logic that earned it cannot drift
# apart (same discipline production_harness.py's method constants followed
# before this file became the single source of truth for both).
FALLBACK_ROUTE_METHOD = ("twilio_log_ingestion._reconstruct_journey: route inferred "
                          "from the last digit of the caller number")
FALLBACK_WAIT_METHOD = ("twilio_log_ingestion._extract_wait_times: fixed 0.1/0.5/0.4 "
                         "split of total call duration across intent_menu/queue/agent")

# NODE ROLES -- see module docstring "NODE ROLES" section. A real event
# source declares one of these on an IVRNodeEvent instead of relying on
# downstream code to guess a stop's kind from its name.
NODE_ROLE_QUEUE = "queue"
NODE_ROLE_AGENT = "agent"
NODE_ROLE_ESCALATION = "escalation"
NODE_ROLES = frozenset({NODE_ROLE_QUEUE, NODE_ROLE_AGENT, NODE_ROLE_ESCALATION})


@dataclass
class IVRNodeEvent:
    """One real, observed stop in a caller's actual path.

    node: the stop's name. If `role` is not also supplied, this name must
        follow the legacy "*queue*" / literal agent_a/b/c/d naming
        convention for downstream intent-inference and resolution-detection
        to recognize it (see module docstring) -- `role` exists precisely
        so a real event source does NOT have to do that anymore.
    wait_seconds: how long the caller spent at this stop. Must be >= 0.
    source: freeform label naming the real system this event came from
        (e.g. "twilio_studio_flow", "taskrouter", "custom_webhook"). Required
        -- an event that will not name its source is the same problem
        EventV1 already refuses for an ESTIMATED value with no method.
    role: OPTIONAL -- one of NODE_ROLES (queue/agent/escalation), naming
        what KIND of stop this is. When present, downstream code trusts
        it over the node's name. When absent, that one stop falls back to
        the name-convention guess -- tagging is per-stop, not all-or-nothing
        for the call.
    """
    node: str
    wait_seconds: float
    source: str
    role: Optional[str] = None


def _validate_ivr_events(raw_events: List[Dict]) -> List[IVRNodeEvent]:
    """Fail-loud validation of the ivr_events contract.

    Malformed real-event data is a defect in the integration that supplied
    it and should surface as one, not silently degrade into the ESTIMATED
    fallback -- see module docstring.
    """
    if not raw_events:
        raise ValueError("ivr_events was provided but is empty; omit the key "
                          "entirely to use the ESTIMATED fallback")
    events: List[IVRNodeEvent] = []
    for i, raw in enumerate(raw_events):
        node = raw.get("node")
        wait_seconds = raw.get("wait_seconds")
        source = raw.get("source")
        role = raw.get("role")
        if not node or not isinstance(node, str):
            raise ValueError(f"ivr_events[{i}] missing a non-empty string 'node'")
        if wait_seconds is None or not isinstance(wait_seconds, (int, float)):
            raise ValueError(f"ivr_events[{i}] ({node!r}) missing a numeric 'wait_seconds'")
        if float(wait_seconds) < 0:
            raise ValueError(f"ivr_events[{i}] ({node!r}) has a negative wait_seconds")
        if not source or not isinstance(source, str):
            raise ValueError(f"ivr_events[{i}] ({node!r}) missing a non-empty string "
                              "'source' -- an event that will not name where it came "
                              "from cannot be stamped VERIFIED")
        if role is not None and role not in NODE_ROLES:
            raise ValueError(f"ivr_events[{i}] ({node!r}) has an unrecognized role "
                              f"{role!r}; must be one of {sorted(NODE_ROLES)} or "
                              "omitted entirely to fall back to name-convention "
                              "detection for this stop")
        events.append(IVRNodeEvent(node=node, wait_seconds=float(wait_seconds),
                                   source=source, role=role))
    seen_roles: Dict[str, str] = {}
    for e in events:
        if e.role is None:
            continue
        prior = seen_roles.get(e.node)
        if prior is not None and prior != e.role:
            raise ValueError(
                f"ivr_events declares conflicting roles for node {e.node!r}: "
                f"{prior!r} and {e.role!r} -- a real event source should not "
                "call the same stop two different things")
        seen_roles[e.node] = e.role
    return events

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
    # Defaulted so every existing caller that builds an IcebergJourney
    # directly (tests, fixtures) keeps working unchanged. A journey built
    # via parse_call_log always sets these explicitly, from whichever path
    # (real ivr_events vs. fallback heuristic) actually produced it -- see
    # module docstring. route_* and wait_* are separate fields on purpose:
    # this version's two ingestion paths always set both together, but
    # nothing about a caller/kernel reading them assumes that stays true.
    route_provenance: str = PROVENANCE_ESTIMATED
    route_method: str = FALLBACK_ROUTE_METHOD
    wait_provenance: str = PROVENANCE_ESTIMATED
    wait_method: str = FALLBACK_WAIT_METHOD
    # node -> role (NODE_ROLE_QUEUE/AGENT/ESCALATION), for whichever nodes
    # a real ivr_events source tagged (see NODE ROLES in module docstring).
    # Empty on the fallback heuristic path -- the heuristic doesn't know
    # roles, only the name convention it already generates by construction.
    # A node absent from this dict simply wasn't tagged; downstream code
    # falls back to the name convention for that node only.
    node_roles: Dict[str, str] = field(default_factory=dict)

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
    
    def __init__(self, cassette=None):
        """Initialize Twilio parser with optional cassette for config.
        
        If cassette is provided, _count_friction will read thresholds
        from it; otherwise falls back to hardcoded defaults.
        """
        self.cassette = cassette

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

        # Real per-node events take precedence over the ingest-side guess.
        # Absent entirely -> unchanged fallback heuristic (ESTIMATED).
        # Present but malformed -> fail loud (see module docstring).
        raw_ivr_events = twilio_record.get("ivr_events")
        if raw_ivr_events is not None:
            events = _validate_ivr_events(raw_ivr_events)
            journey = self._reconstruct_journey_from_events(events)
            wait_times = self._wait_times_from_events(events)
            node_roles = self._node_roles_from_events(events)
            sources = ", ".join(sorted({e.source for e in events}))
            route_provenance = wait_provenance = PROVENANCE_VERIFIED
            route_method = f"twilio_log_ingestion: real per-node events from {sources}"
            wait_method = route_method
        else:
            # Reconstruct journey from call data
            # In real system, would parse IVR logs/recordings
            journey = self._reconstruct_journey(twilio_record)
            wait_times = self._extract_wait_times(twilio_record, journey)
            node_roles = {}  # heuristic path knows no roles -- see module docstring
            route_provenance = wait_provenance = PROVENANCE_ESTIMATED
            route_method = FALLBACK_ROUTE_METHOD
            wait_method = FALLBACK_WAIT_METHOD

        # Calculate friction (ingest-side heuristic ESTIMATE -- see
        # _count_friction; the production harness measures its own
        # friction from wait_times against the cassette threshold)
        friction_count = self._count_friction(twilio_record, journey, cassette=self.cassette)
        
        # Determine abandonment reason
        abandonment_reason = None if resolved else outcome["reason"]
        
        return IcebergJourney(
            caller_id=f"twilio_{sid[:8]}",
            timestamp=timestamp,
            journey=journey,
            wait_times=wait_times,
            total_duration=float(duration),
            resolved=resolved,
            friction_count=friction_count,
            abandonment_reason=abandonment_reason,
            route_provenance=route_provenance,
            route_method=route_method,
            wait_provenance=wait_provenance,
            wait_method=wait_method,
            node_roles=node_roles,
        )

    def _reconstruct_journey_from_events(self, events: List[IVRNodeEvent]) -> List[str]:
        """Build a journey from real per-node events, in the order given.

        Keeps the existing "root" / "exit" bookends other code doesn't
        depend on but tests and log-reading humans already expect; the
        real stops in between are exactly the node names the caller
        supplied, unrenamed (see module docstring on the naming convention
        those names still need to follow).
        """
        return ["root"] + [e.node for e in events] + ["exit"]

    def _wait_times_from_events(self, events: List[IVRNodeEvent]) -> Dict[str, float]:
        """Real per-node wait times, keyed by the real node names.

        Later events for a repeated node name add to that node's total --
        a caller who visits the same queue twice had two real waits there,
        not one overwriting the other.
        """
        waits: Dict[str, float] = {}
        for e in events:
            waits[e.node] = waits.get(e.node, 0.0) + e.wait_seconds
        return waits

    def _node_roles_from_events(self, events: List[IVRNodeEvent]) -> Dict[str, str]:
        """node -> role, for whichever events declared one.

        Conflicting roles for a repeated node are already refused by
        _validate_ivr_events -- by the time events reach here every
        node's role (if any) is unambiguous, so a plain last-write-wins
        merge is safe.
        """
        roles: Dict[str, str] = {}
        for e in events:
            if e.role is not None:
                roles[e.node] = e.role
        return roles
    
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
    
    def _count_friction(self, record: Dict, journey: List[str], cassette=None) -> int:
        """Estimate friction from call patterns.

        Item #7 scope: these duration heuristics are an ingest-side ESTIMATE
        and deliberately NOT unified with governance/friction_core in Items
        #4-#6; they unify when the ingest path is integrated into the
        production flow. The production harness does NOT use this estimate on
        the governance path -- it measures friction itself from wait_times
        against the cassette's threshold.
        
        Thresholds MUST be read from cassette (fail-loud, no silent fallback).
        If cassette is None or lacks required Twilio thresholds, raises error.
        """
        
        if cassette is None:
            raise ValueError(
                "_count_friction requires a cassette with Twilio thresholds; "
                "cassette=None is not permitted"
            )
        
        # Validate cassette and read parameters with fail-loud semantics
        # (same as production_harness.py -- no defaults, KeyError if missing)
        try:
            params = validate_cassette(cassette)
        except Exception as e:
            raise ValueError(f"Cassette validation failed: {e}")

        # Twilio ingest is the telephony_ingest capability's surface:
        # a cassette that doesn't enable it has no twilio_* thresholds
        # BY DESIGN (they'd be rejected as unowned declarations), so
        # refuse here with the capability error, not a KeyError deep
        # in parameter access.
        from cassette_capabilities import (
            CAPABILITY_TELEPHONY_INGEST,
            CapabilityError,
            require_capabilities,
        )
        try:
            require_capabilities(
                cassette, (CAPABILITY_TELEPHONY_INGEST,),
                consumer="TwilioLogParser._count_friction",
            )
        except CapabilityError as e:
            raise ValueError(str(e))
        
        # Read Twilio thresholds with NO fallback (type-strict, fail-loud)
        try:
            long_threshold = params.int_value("twilio_long_duration_threshold")
            medium_threshold = params.int_value("twilio_medium_duration_threshold")
            short_threshold = params.int_value("twilio_short_duration_threshold")
        except KeyError as e:
            raise ValueError(f"Cassette missing required Twilio threshold: {e}")
        
        friction = 0
        duration = int(record.get("duration", 0))
        
        # Long calls suggest friction (cassette-configurable)
        if duration > long_threshold:
            friction += 2
        elif duration > medium_threshold:
            friction += 1
        
        # Multiple queue visits suggest repeats
        queue_visits = sum(1 for node in journey if "queue" in node)
        if queue_visits > 1:
            friction += queue_visits - 1
        
        # Short duration might indicate no-answer (friction, cassette-configurable)
        if duration < short_threshold and record.get("status") != "completed":
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
