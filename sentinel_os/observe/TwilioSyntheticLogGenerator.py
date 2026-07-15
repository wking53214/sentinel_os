import json
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from IngestAdapter import generate_synthetic_call

ACCOUNT_SID = "AC" + hashlib.sha256(b"iceberg-synthetic").hexdigest()[:32]
IVR_NUMBER = "+15005550006"
TWIML_BASE = "https://ivr-handler.example.com/twiml/"
BASE_DAY = datetime(2026, 7, 1, tzinfo=timezone.utc)
BUSINESS_OPEN_S = 8 * 3600
BUSINESS_CLOSE_S = 20 * 3600

DEFAULT_JOURNEYS: Dict[str, List[str]] = {
    "billing": ["root", "intent_menu", "billing::menu_1", "billing::auth", "billing::resolved"],
    "claims": ["root", "intent_menu", "claims::menu_1", "claims::auth", "claims::handoff"],
    "pharmacy": ["root", "intent_menu", "pharmacy::menu_1", "pharmacy::auth", "pharmacy::resolved"],
}
DEFAULT_RESOLUTION = frozenset({"billing::resolved", "pharmacy::resolved"})
DEFAULT_HANDOFF = frozenset({"claims::handoff"})

def _iso(seconds_into_day: float) -> str:
    dt = BASE_DAY + timedelta(seconds=round(seconds_into_day, 3))
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _call_sid(seed: int, index: int) -> str:
    return "CA" + hashlib.sha256(f"iceberg:{seed}:{index}".encode()).hexdigest()[:32]

def _from_number(seed: int, index: int) -> str:
    d = int(hashlib.sha256(f"from:{seed}:{index}".encode()).hexdigest()[:8], 16)
    return f"+1500555{d % 10000:04d}"

def twilio_event_schema(call_sid: str, event_type: str, timestamp_iso: str,
                        sequence_number: int, from_number: str, **kwargs: Any) -> Dict[str, Any]:
    ev: Dict[str, Any] = {
        "CallSid": call_sid,
        "AccountSid": ACCOUNT_SID,
        "To": IVR_NUMBER,
        "From": from_number,
        "timestamp": timestamp_iso,
        "type": event_type,
        "SequenceNumber": sequence_number,
    }
    ev.update(kwargs)
    return ev

def generate_twilio_population(n_calls: int, seed: int = 815,
                               journeys: Optional[Dict[str, List[str]]] = None) -> str:
    jmap = DEFAULT_JOURNEYS if journeys is None else journeys
    rng = random.Random(seed)
    intents = list(jmap.keys())
    all_events: List[Dict[str, Any]] = []
    for i in range(n_calls):
        intent = intents[i % len(intents)]
        offset = rng.uniform(BUSINESS_OPEN_S, BUSINESS_CLOSE_S)
        call_sid = _call_sid(seed, i)
        from_number = _from_number(seed, i)
        internal = generate_synthetic_call(call_sid, jmap[intent], rng, "clean")
        seq = 0
        t = float(offset)
        for e in internal:
            ts = _iso(t + e["timestamp"])
            etype = e["type"]
            if etype == "call_start":
                all_events.append(twilio_event_schema(call_sid, "initiated", ts, seq, from_number, CallStatus="in-progress"))
            elif etype == "menu_reached":
                all_events.append(twilio_event_schema(call_sid, "twiml_menu", ts, seq, from_number, Url=TWIML_BASE + e["node"]))
            elif etype == "hold_start":
                all_events.append(twilio_event_schema(call_sid, "queued", ts, seq, from_number, QueueSid="QU" + call_sid[-16:]))
            elif etype == "hold_end":
                all_events.append(twilio_event_schema(call_sid, "dequeued", ts, seq, from_number, QueueSid="QU" + call_sid[-16:]))
            elif etype == "call_end":
                all_events.append(twilio_event_schema(call_sid, "completed", ts, seq, from_number, CallStatus="completed"))
            seq += 1
    all_events.sort(key=lambda e: (e["timestamp"], e["CallSid"], e["SequenceNumber"]))
    return "\n".join(json.dumps(e, separators=(",", ":")) for e in all_events) + "\n"

def load_twilio_jsonl(text: str) -> Dict[str, List[Dict[str, Any]]]:
    calls: Dict[str, List[Dict[str, Any]]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        calls.setdefault(e["CallSid"], []).append(e)
    return calls
