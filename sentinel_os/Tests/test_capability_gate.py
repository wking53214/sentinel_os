"""The capability gate, proven without a telephony consumer.

Until now the only proof that require_capabilities actually refuses a
cassette lived in test_cassette_capabilities.py, and it proved it by
handing a banking cassette to SentinelCore and CassetteHarness. Both of
those are telephony pipelines on their way to GSA-815, which would have
left the kernel holding the gate with nothing demonstrating that it
bites.

So this file proves the same guarantee with a stand-in consumer: a
name and a required-capability tuple, no pipeline behind it. Real
cassettes, real gate, no telephony code.

It also proves the gate does not OVER-bite -- a cassette that enables
what is asked of it passes. A gate that refuses everything would keep
the refusal tests green while being useless.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassette_capabilities import (
    CAPABILITY_OUTCOME_OBLIGATION,
    CAPABILITY_RL,
    CAPABILITY_ROUTING_TOPOLOGY,
    CAPABILITY_SELF_HEALING,
    CAPABILITY_TELEPHONY_INGEST,
    CapabilityError,
    require_capabilities,
)
from cassettes.banking_cassette import BankingCassette
from cassettes.mortgage_cassette import MortgageCassette

STAND_IN = "StandInPipeline"


# ---- the gate refuses ---------------------------------------------------------

def test_kernel_only_cassette_refused_by_a_telephony_consumer():
    """Mortgage enables outcome_obligation and nothing else. A consumer
    that reads the telephony surface must be refused at the door."""
    with pytest.raises(CapabilityError) as exc:
        require_capabilities(MortgageCassette(),
                             (CAPABILITY_TELEPHONY_INGEST,), consumer=STAND_IN)
    msg = str(exc.value)
    assert STAND_IN in msg, "error must name the consumer that refused"
    assert "telephony_ingest" in msg, "error must name what was missing"
    assert "mortgage" in msg, "error must name the cassette that was refused"


def test_partially_capable_cassette_refused_and_error_names_only_the_gap():
    """Banking enables routing but not telephony. Asking for both is a
    refusal, and the message points at telephony rather than implying
    the cassette enables nothing."""
    with pytest.raises(CapabilityError) as exc:
        require_capabilities(
            BankingCassette(),
            (CAPABILITY_TELEPHONY_INGEST, CAPABILITY_ROUTING_TOPOLOGY),
            consumer=STAND_IN)
    msg = str(exc.value)
    assert "telephony_ingest" in msg
    assert "routing_topology" in msg, "the enabled set is reported too"
    assert "banking" in msg


# ---- the gate does not over-bite ---------------------------------------------

def test_cassette_that_enables_what_is_asked_passes():
    """Banking's own manifest: routing, rl, self-healing. Asking for
    exactly that must NOT raise."""
    require_capabilities(
        BankingCassette(),
        (CAPABILITY_ROUTING_TOPOLOGY, CAPABILITY_RL, CAPABILITY_SELF_HEALING),
        consumer=STAND_IN)


def test_kernel_only_cassette_passes_a_kernel_only_requirement():
    require_capabilities(MortgageCassette(),
                         (CAPABILITY_OUTCOME_OBLIGATION,), consumer=STAND_IN)


def test_empty_requirement_passes_for_any_cassette():
    """Asking for nothing refuses nothing -- the gate is driven by what
    the consumer declares it reads, not by a default denial."""
    require_capabilities(MortgageCassette(), (), consumer=STAND_IN)
    require_capabilities(BankingCassette(), (), consumer=STAND_IN)
