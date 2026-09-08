"""An unbound harness says so on every result.

Measured before this existed: GovernanceHarness(OFFLINE_CONFIG, ...,
require_cassette_binding=False) with no reachable ledger returned a fully
populated governed result -- governed, approved, reasoning, model identity,
cost -- with no key naming the ledger at all. The decision was written
nowhere and nothing in the output said so. The opt-out itself is unchanged;
its record is not.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cassettes.mortgage_cassette import MortgageCassette  # noqa: E402
from governance_harness import GovernanceHarness  # noqa: E402
from test_governance_harness import OFFLINE_CONFIG, StubDecider, _clean_episode  # noqa: E402


def _unbound():
    harness = GovernanceHarness(OFFLINE_CONFIG, MortgageCassette(),
                                require_cassette_binding=False)
    harness.decider = StubDecider()
    return harness


def test_an_unbound_harness_says_so_on_a_governed_result(caplog):
    harness = _unbound()
    assert harness.ledger is None
    with caplog.at_level(logging.WARNING, logger="sentinel_os.governance_harness"):
        result = harness.process(_clean_episode("E-unbound-1"), issue_count=1)
    assert result["governed"] is True
    assert result["ledger_bound"] is False
    assert result["decision_recorded"] is False
    assert any("require_cassette_binding=True" in r.getMessage() for r in caplog.records), \
        "the warning must name the remedy"


def test_an_ungoverned_result_still_says_whether_a_ledger_exists():
    harness = _unbound()
    result = harness.process(_clean_episode("E-unbound-2"), issue_count=0)
    assert result["governed"] is False
    assert result["ledger_bound"] is False
    # Nothing is ever written for an ungoverned episode, bound or not, so
    # decision_recorded would be a claim about a write that never happens.
    assert "decision_recorded" not in result


def test_a_bound_harness_records_and_says_so(caplog):
    harness = _unbound()
    written = []
    harness.ledger = object()  # bound, as far as process() is concerned
    harness._write_decision = lambda episode, params, issue_count, decision: written.append(episode.episode_id)
    with caplog.at_level(logging.WARNING, logger="sentinel_os.governance_harness"):
        result = harness.process(_clean_episode("E-bound-1"), issue_count=1)
    assert written == ["E-bound-1"]
    assert result["ledger_bound"] is True
    assert result["decision_recorded"] is True
    assert not [r for r in caplog.records if "NOT written" in r.getMessage()]
