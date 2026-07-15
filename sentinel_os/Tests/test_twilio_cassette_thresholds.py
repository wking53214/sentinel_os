"""
Test that Twilio ingest reads friction thresholds from cassette.

Item #7 verification: _count_friction now accepts a cassette parameter
and reads twilio_long_duration_threshold, twilio_medium_duration_threshold,
and twilio_short_duration_threshold from get_governance_parameters().
Falls back to hardcoded defaults (300/120/10) if no cassette provided.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from twilio_log_ingestion import TwilioLogParser
from cassettes.ivr_cassette import IvrCassette


def test_count_friction_uses_cassette_thresholds():
    """_count_friction reads thresholds from cassette instead of hardcodes."""
    parser = TwilioLogParser()
    cassette = IvrCassette()
    
    # A call lasting 250 seconds (< 300 but > 120)
    record_medium = {
        "duration": 250,
        "status": "completed"
    }
    
    # With default cassette (300/120/10), should get 1 friction
    friction = parser._count_friction(record_medium, [], cassette=cassette)
    assert friction == 1, f"Expected 1 friction for 250s call, got {friction}"


def test_count_friction_requires_cassette():
    """_count_friction is fail-loud: cassette=None raises rather than
    silently falling back to hardcoded defaults. A friction estimate with
    no declared source of truth for its thresholds is a policy the system
    invented, not one that governed anything -- so it's refused outright."""
    parser = TwilioLogParser()

    record_medium = {
        "duration": 250,
        "status": "completed"
    }

    try:
        parser._count_friction(record_medium, [], cassette=None)
        assert False, "expected ValueError for cassette=None, got a result instead"
    except ValueError as e:
        assert "cassette" in str(e).lower()


def test_count_friction_long_call():
    """Calls over long_duration_threshold get 2 friction."""
    parser = TwilioLogParser()
    cassette = IvrCassette()
    
    # 350 seconds > 300 (long threshold)
    record_long = {
        "duration": 350,
        "status": "completed"
    }
    
    friction = parser._count_friction(record_long, [], cassette=cassette)
    assert friction == 2, f"Expected 2 friction for 350s call (> long threshold), got {friction}"


def test_count_friction_short_call_incomplete():
    """Short incomplete calls indicate dropped calls (friction)."""
    parser = TwilioLogParser()
    cassette = IvrCassette()
    
    # 5 seconds < 10 (short threshold), not completed
    record_short_incomplete = {
        "duration": 5,
        "status": "abandoned"
    }
    
    friction = parser._count_friction(record_short_incomplete, [], cassette=cassette)
    assert friction == 1, f"Expected 1 friction for short incomplete call, got {friction}"


def test_count_friction_short_call_completed():
    """Short completed calls are normal (no extra friction)."""
    parser = TwilioLogParser()
    cassette = IvrCassette()
    
    # 5 seconds < 10 (short threshold), but completed
    record_short_completed = {
        "duration": 5,
        "status": "completed"
    }
    
    friction = parser._count_friction(record_short_completed, [], cassette=cassette)
    assert friction == 0, f"Expected 0 friction for short completed call, got {friction}"


def test_count_friction_with_queue_repeats():
    """Multiple queue visits add friction."""
    parser = TwilioLogParser()
    cassette = IvrCassette()
    
    # Journey with 3 queue visits
    journey = ["root", "intent_menu", "billing_queue", "transfer_queue", "support_queue"]
    record = {
        "duration": 150,
        "status": "completed"
    }
    
    friction = parser._count_friction(record, journey, cassette=cassette)
    # 150s: between 120 and 300, so +1 for duration
    # 3 queue visits: +2 for repeats (queue_visits - 1 = 3 - 1 = 2)
    assert friction == 3, f"Expected 3 friction (1 for duration + 2 for repeats), got {friction}"
