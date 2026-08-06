"""
GovernanceHarness -- the kernel's domain-agnostic governance path.

Built 2026-08-05 ALONGSIDE production_harness.py, not replacing it yet.
sentinel_worker.py depends directly on IcebergProductionHarness
("one worker = one long-lived IcebergProductionHarness") and the
transplant onto this harness is deliberately deferred -- see
/areas/gsa-815.md for the reasoning. Nothing here touches
production_harness.py, cassette_harness.py, or anything that currently
imports them.

Built from the acceptance spec and the behavior catalog extracted from
every harness-touching test in the repo (30+ tests across 9 files, not
just the eleven in test_cassette_governs_every_decision.py) -- not from
reading production_harness.py's code. That file was deliberately not
opened while this was designed, to avoid inheriting its three-layer
telephony shape by osmosis rather than by decision.

WHAT THIS CLOSES: episode.judge_episode is two lines -- validate, then
let the cassette score. Nothing in the kernel ever decided whether a
judged episode needed a governor's look, or wrote that decision
anywhere durable. That rule lived as one line inside
production_harness.py, IVR-shaped (a friction-count comparison against
telephony wait times). This is that governance path, generalized.

THE ONE GENUINE DESIGN CALL: "how many issues does this episode have"
is domain-specific -- IVR counts long-wait breaches from raw call data
BEFORE judging (friction_core.compute_friction); mortgage counts
requested/actual mismatches plus thin outcome reasons DURING scoring
(MortgageCassette._score_components). These are different computations
over different data, not one generic count two domains happen to
share. So this harness does not compute it. Whoever assembles the
Episode -- already domain-specific, already the caller's job per
test_live_path_kernel_wiring.py's own pattern -- computes the count and
hands it to process() alongside the episode. The harness's job is
purely mechanical from there: compare the count to the cassette's
declared governance_trigger (inclusive >=), consult the governor if
tripped, write the full policy snapshot to the ledger. Flagging this
plainly because it's the one place nothing in the spec or the tests
spelled out the answer for me.

Deliberately excluded (see /areas/gsa-815.md, Aug 5 build-alongside
discussion): batch processing (spec says one unit at a time) and
Prometheus metrics export (already classified GSA-815/telephony in
DEPENDENCIES.md, and nothing in the spec asked for a domain-agnostic
metrics vocabulary). Also excluded for now: the outcome-obligation
declaration behavior test_live_path_kernel_wiring.py revealed
(capability-driven maturation-horizon string) -- real and worth having,
but outside the nine-bullet spec Wm gave; flagged as a follow-on, not
built silently.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from cassette_capabilities import CAPABILITY_TELEPHONY_INGEST, CapabilityError
from cassette_forensics import (
    compute_cassette_code_hash,
    compute_cassette_hash,
    serialize_cassette_for_ledger,
)
from cassette_interface import Cassette
from cassette_schema import validate_cassette
from episode import Episode, explain_episode, judge_episode
from governance_decider import GovernanceDecider


class GovernanceHarness:
    """Domain-agnostic governance path: judge, decide whether to govern,
    consult the governor, write the ledger. No telephony, no queues, no
    call-shaped anything -- that surface is production_harness.py /
    cassette_harness.py's, in GSA-815.

    Deliberately refuses any cassette declaring CAPABILITY_TELEPHONY_INGEST
    -- the INVERSE of production_harness.py's own posture, which refuses
    everything BUT telephony. Two harnesses, two capability doors, same
    kernel judgment underneath.
    """

    def __init__(self, config: Dict[str, Any], cassette: Cassette,
                require_cassette_binding: bool = True,
                decider: Optional[GovernanceDecider] = None):
        self._refuse_if_telephony(cassette)
        validate_cassette(cassette)

        self.cassette = cassette
        self.require_cassette_binding = require_cassette_binding
        self.decider = decider if decider is not None else GovernanceDecider(
            api_key=config.get("claude_api_key"))
        self.ledger = self._connect_ledger(config)

        if self.ledger is None and require_cassette_binding:
            raise RuntimeError(
                "require_cassette_binding is True but no ledger is "
                "reachable -- refusing to start unbound. Pass "
                "require_cassette_binding=False for an explicit "
                "dev/offline run."
            )
        if self.ledger is not None:
            self._bind_current_cassette()

    # ---- construction/swap: the telephony door ----

    @staticmethod
    def _refuse_if_telephony(cassette: Cassette) -> None:
        caps = tuple(getattr(cassette, "CAPABILITIES", ()) or ())
        if CAPABILITY_TELEPHONY_INGEST in caps:
            domain = cassette.get_config().domain
            raise CapabilityError(
                f"GovernanceHarness refuses cassette '{domain}': declares "
                f"{CAPABILITY_TELEPHONY_INGEST}, a call-center capability. "
                "This is the kernel's domain-agnostic harness -- telephony "
                "cassettes belong on production_harness.py/CassetteHarness."
            )

    def swap_cassette(self, cassette: Cassette) -> None:
        """Replace the governing cassette. Read at decision time, not
        cached -- the very next process() call sees it. Refuses (and
        does not replace the current cassette) exactly as construction
        does: telephony rejected, an invalid cassette rejected."""
        self._refuse_if_telephony(cassette)
        validate_cassette(cassette)
        self.cassette = cassette
        if self.ledger is not None:
            self._bind_current_cassette()

    # ---- ledger connection / binding ----

    def _connect_ledger(self, config: Dict[str, Any]):
        if not config.get("postgres_host"):
            return None
        from governance.ledger_postgres import PostgreSQLLedger
        try:
            return PostgreSQLLedger(
                host=config["postgres_host"],
                port=config.get("postgres_port", 5432),
                dbname=config.get("postgres_db", "iceberg"),
                user=config.get("postgres_user", "iceberg"),
                password=config.get("postgres_password", "iceberg"),
            )
        except Exception as exc:
            if self.require_cassette_binding:
                raise RuntimeError(
                    "require_cassette_binding is True but the ledger "
                    "connection failed -- refusing to start bound-but-"
                    f"broken, same posture as no ledger configured at "
                    f"all. Underlying error: {type(exc).__name__}: {exc}"
                ) from exc
            return None

    def _bind_current_cassette(self) -> None:
        params = validate_cassette(self.cassette)
        snapshot = serialize_cassette_for_ledger(params)
        cassette_hash = compute_cassette_hash(snapshot)
        code_hash = compute_cassette_code_hash(self.cassette)
        self.ledger.bind_cassette_version(
            params.cassette_version, cassette_hash, code_hash)

    # ---- the one unit of work ----

    def process(self, episode: Episode, issue_count: int) -> Dict[str, Any]:
        """Judge one episode, decide whether it needs governing, consult
        the governor if so, write the ledger, return the result.

        `issue_count` is supplied by the caller -- see module docstring
        for why this harness does not compute it itself.

        Fail-closed contract: an unusable governor answer withholds the
        ACTION (governance_approved stays False, governance_blocked
        becomes True) but never aborts the pipeline -- judgment still
        ran and the result still carries it.
        """
        params = validate_cassette(self.cassette)
        trigger = params.int_value("governance_trigger")
        quality = judge_episode(self.cassette, episode)
        factors = explain_episode(self.cassette, episode)
        governance_required = issue_count >= trigger

        result: Dict[str, Any] = {
            "episode_id": episode.episode_id,
            "cassette_version": params.cassette_version,
            "quality": quality,
            "factors": factors,
            "issue_count": issue_count,
            "governance_trigger": trigger,
            "governance_required": governance_required,
            "governed": False,
            "governance_approved": False,
            "governance_blocked": False,
        }
        if not governance_required:
            return result

        try:
            decision = self.decider.safety_check(
                "episode_judgment",
                {"cassette_version": params.cassette_version,
                 "domain": self.cassette.get_config().domain,
                 "issue_count": issue_count, "tier": quality.tier},
            )
            if not isinstance(decision, dict):
                raise TypeError(
                    f"decider.safety_check returned {type(decision).__name__}, not a dict")
        except Exception as exc:
            # Defense in depth: GovernanceDecider already fails closed
            # internally and never raises, but process()'s own
            # "unusable answer withholds the action, never kills the
            # pipeline" guarantee should not depend on every decider
            # implementation replicating that discipline correctly.
            # A decider that raises (or returns garbage) is the most
            # unusable answer there is; it gets the same fail-closed
            # treatment as one that returns a malformed dict.
            decision = {
                "safe": False,
                "reasoning": f"decider_error: {type(exc).__name__}: {exc}",
                "model_identity": None, "cost": None,
            }
        # `safe is True`, not `bool(safe)`: a decider that returns a
        # non-bool truthy value for "safe" (a string like "yes", a
        # nonzero number) must NOT be treated as an approval just
        # because Python considers it truthy. GovernanceDecider itself
        # already guards against this internally; this is the same
        # guard held at the harness boundary too, so a non-compliant
        # decider implementation can't slip a non-bool "safe" through.
        safe = decision.get("safe")
        approved = safe is True

        result["governed"] = True
        result["governance_approved"] = approved
        result["governance_blocked"] = not approved
        result["reasoning"] = decision.get("reasoning")
        result["model_identity"] = decision.get("model_identity")
        result["ai_cost"] = decision.get("cost")

        if self.ledger is not None:
            self._write_decision(episode, params, issue_count, decision)

        return result

    def _write_decision(self, episode: Episode, params, issue_count: int,
                        decision: Dict[str, Any]) -> None:
        from governance.ledger_postgres import GovernanceDecisionRecord
        record = GovernanceDecisionRecord(
            action_type="governance_decision",
            node=self.cassette.get_config().domain,
            cassette_version=params.cassette_version,
            input_data={
                "episode_id": episode.episode_id,
                "issue_count": issue_count,
                "governance_trigger": params.int_value("governance_trigger"),
            },
            policy_parameters=serialize_cassette_for_ledger(params),
            reasoning=decision.get("reasoning") or "",
            output={"approved": bool(decision.get("safe"))},
            model_identity=decision.get("model_identity"),
            ai_cost=decision.get("cost"),
        )
        self.ledger.append_decision(record, governance_params=params)

    # ---- housekeeping ----

    def shutdown(self) -> None:
        if self.ledger is not None:
            self.ledger.close()
