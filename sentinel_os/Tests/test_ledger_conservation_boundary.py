"""The conservation boundary is enforced at the ledger, not only in the harness.

governance_harness verified episode -> judgment before it wrote, but the check
lived in the harness: a caller that went straight to PostgreSQLLedger.append_decision
(GSA-815's governed pipeline did) got durable state with no verification. These
tests hold the ledger to the rule itself:

  - a decision with no episode is refused and nothing is written;
  - the dev-only override permits a missing episode, loudly, and nothing else;
  - a given episode is always verified, override or not;
  - a rejection, and a failure of the check itself, both leave the chain untouched.

The suite runs with the override on (conftest.py) because most tests append
hand-built records; every test here turns it off for itself.
"""
import inspect

import pytest

from conservation_kernel import AuthorityStatus, EpistemicStatus, OriginStatus, Proposition

from episode import EpisodeEvent, make_episode
from governance.ledger_postgres import (
    ENV_CONSERVATION_DEV_OVERRIDE,
    GovernanceDecisionRecord,
)


def _episode(**over):
    base = dict(
        episode_id="ep-ledger-boundary-1", domain="mortgage",
        requested={"approve": True, "rate": 6.5},
        actual={"approved": False, "dti": 0.47, "reason_code": "DTI_EXCEEDED"},
        actor_report={"approved": False, "confidence": 0.9},
        outcome_reasons=("DTI 0.47 exceeds 0.43 policy cap",),
        timeline=(EpisodeEvent(at=0.0, kind="application_received", detail={}),),
        attributes={"duration_s": 3.4, "friction_count": 1},
    )
    base.update(over)
    return make_episode(**base)


def _record():
    return GovernanceDecisionRecord(
        action_type="governance_decision", node="mortgage", cassette_version="mortgage-v2.1.0",
        input_data={"episode_id": "ep-ledger-boundary-1", "issue_count": 1},
        policy_parameters={"dti_cap": 0.43},
        reasoning="DTI 0.47 exceeds the 0.43 cap; denial is consistent with the observed record.",
        output={"approved": False},
    )


def _count(ledger):
    return len(ledger.get_decisions(limit=1000))


@pytest.fixture
def enforced(monkeypatch):
    """The real configuration: the dev override is off."""
    monkeypatch.delenv(ENV_CONSERVATION_DEV_OVERRIDE, raising=False)


def _make_the_kernel_reject(monkeypatch):
    """Coerce the transformer into emitting a judgment with no lineage to any observed fact."""
    from conservation.judgment import GovernanceJudgmentTransformer as T

    def _unrooted_proposal(self, request, record):
        source = request.input_artifact
        bad = Proposition(
            proposition_id="p-governance-judgment",
            text="Blocked, with no lineage to any observed fact.",
            epistemic_status=EpistemicStatus.DECISION,
            origin=OriginStatus.MACHINE_ORIGINATED,
            authority=AuthorityStatus.PROPOSED,
        )
        output = self._artifact(
            request, suffix="judgment", content="unrooted judgment",
            propositions=source.propositions + (bad,),
        )
        return self._proposal(request, output)

    monkeypatch.setattr(T, "build_proposal", _unrooted_proposal)


def test_a_decision_with_no_episode_is_refused_and_writes_nothing(test_ledger, enforced):
    before = _count(test_ledger)
    with pytest.raises(ValueError, match="conservation boundary"):
        test_ledger.append_decision(_record())
    assert _count(test_ledger) == before


def test_the_refusal_names_the_way_out_so_it_is_not_a_mystery(test_ledger, enforced):
    with pytest.raises(ValueError) as exc:
        test_ledger.append_decision(_record())
    assert "episode=" in str(exc.value) and ENV_CONSERVATION_DEV_OVERRIDE in str(exc.value)


def test_a_decision_with_a_clean_episode_is_written(test_ledger, enforced):
    before = _count(test_ledger)
    assert test_ledger.append_decision(_record(), episode=_episode())
    assert _count(test_ledger) == before + 1


def test_the_dev_override_permits_a_missing_episode_and_says_so(test_ledger, monkeypatch, capsys):
    import governance.ledger_postgres as lp
    monkeypatch.setenv(ENV_CONSERVATION_DEV_OVERRIDE, "1")
    monkeypatch.setattr(lp, "_conservation_override_warned", False)
    before = _count(test_ledger)
    assert test_ledger.append_decision(_record())
    assert _count(test_ledger) == before + 1
    err = capsys.readouterr().err
    assert ENV_CONSERVATION_DEV_OVERRIDE in err and "NO conservation verification" in err


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_a_falsy_override_is_not_an_override(test_ledger, monkeypatch, value):
    monkeypatch.setenv(ENV_CONSERVATION_DEV_OVERRIDE, value)
    with pytest.raises(ValueError, match="conservation boundary"):
        test_ledger.append_decision(_record())


def test_a_decision_the_kernel_rejects_is_refused_and_writes_nothing(test_ledger, enforced, monkeypatch):
    from conservation.boundary import ConservationBoundaryRejected
    _make_the_kernel_reject(monkeypatch)
    before = _count(test_ledger)
    with pytest.raises(ConservationBoundaryRejected) as exc:
        test_ledger.append_decision(_record(), episode=_episode())
    assert any(r.code == "UNROOTED_NEW_PROPOSITION" for r in exc.value.decision.rejections)
    assert _count(test_ledger) == before


def test_the_override_does_not_waive_a_given_episode(test_ledger, monkeypatch):
    """The override only permits a MISSING episode. Handing the ledger one means it is verified."""
    from conservation.boundary import ConservationBoundaryRejected
    monkeypatch.setenv(ENV_CONSERVATION_DEV_OVERRIDE, "1")
    _make_the_kernel_reject(monkeypatch)
    before = _count(test_ledger)
    with pytest.raises(ConservationBoundaryRejected):
        test_ledger.append_decision(_record(), episode=_episode())
    assert _count(test_ledger) == before


def test_a_failure_of_the_check_itself_is_a_refusal_not_a_pass(test_ledger, enforced, monkeypatch):
    import conservation.boundary as boundary

    def _boom(episode, record):
        raise ValueError("verifier exploded")

    monkeypatch.setattr(boundary, "verify_governed_decision", _boom)
    before = _count(test_ledger)
    with pytest.raises(RuntimeError, match="decision not persisted"):
        test_ledger.append_decision(_record(), episode=_episode())
    assert _count(test_ledger) == before


def test_the_harness_hands_the_ledger_its_episode():
    """Without this the harness would fall into the ledger's missing-episode refusal in production."""
    from governance_harness import GovernanceHarness
    source = inspect.getsource(GovernanceHarness._write_decision)
    assert "episode=episode" in source
