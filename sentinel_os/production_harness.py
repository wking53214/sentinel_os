"""
Production Harness - Ties all systems together

Real Twilio → Real Prometheus → Real PostgreSQL → Real Claude → Real Governance
"""

import os
import time
from typing import Dict

# Import all production components
from twilio_log_ingestion import (
    TwilioLogParser, TwilioStreamAdapter,
    FALLBACK_ROUTE_METHOD, FALLBACK_WAIT_METHOD,
    NODE_ROLE_QUEUE,
)
from metrics_prometheus import PrometheusMetrics
from governance.ledger_postgres import PostgreSQLLedger
from claude_governance_api import ClaudeGovernanceDecider
from observe_perceive_core import ObserveCore, FrictionEvent
from sentinel_core import SentinelCore
from cassette_loader import CassetteLoader
from cassette_schema import validate_cassette
from tracing import tracer, mark_error
from governance.ledger_postgres import GovernanceDecisionRecord
from governance.friction_core import compute_friction
from episode import EpisodeIntegrityError, judge_episode
from event_v1 import (EventIntegrityError, PROVENANCE_ESTIMATED,
                      PROVENANCE_VERIFIED, assemble_episode,
                      estimated_fields, make_event)
from cassette_capabilities import CAPABILITY_OUTCOME_OBLIGATION
from queue_staffing_bayes_integration import (
    StaffingCoordinator, BayesianIntentEngine
)
from operational_resilience import setup_logging
from circuit_breaker import CircuitBreaker
from governance_loop_guard import PipelineStateEngine
from cassette_forensics import (
    compute_cassette_code_hash, compute_cassette_hash,
    serialize_cassette_for_ledger,
)

logger = setup_logging("IcebergProductionHarness")


def _safe_code_hash(cassette_obj):
    """Compute the cassette CODE hash without ever raising (Item 3).

    A failure to hash the decision code must NOT crash a governance decision or
    block the ledger write -- so any exception yields None, which omits the
    field from the canonical form (the row simply carries no code-hash commitment
    rather than a wrong one). compute_cassette_code_hash is itself fail-closed
    internally (unreadable source becomes a marker string), so this is a
    belt-and-suspenders guard for anything unexpected in the object itself.
    """
    try:
        return compute_cassette_code_hash(cassette_obj)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"cassette code hash unavailable, omitting: {exc}")
        return None


class IcebergProductionHarness:
    """Complete production system: all components wired together"""
    
    def __init__(self, config: Dict, cassette=None,
                 require_cassette_binding: bool = True):
        """Initialize production system.

        The cassette is THE governing policy for this harness. It is
        loaded (or injected), schema-validated fail-loud, and every
        governance number read later in process_call comes from it --
        never from a literal in this file.

        require_cassette_binding (default True, fail-closed): a loaded
        cassette must be content-bound into the ledger (Item 2 /
        bind_cassette_version) before this harness will start. Without
        this, a cassette can govern real decisions with no ledger
        commitment of what content it actually is -- an operator could
        change parameters or code without changing the version string,
        and historical queries by version would silently return rows
        governed by different content. There is no env-var override for
        this in the real production entrypoint (sentinel_worker.py) --
        same posture as ICEBERG_LEDGER_RUNTIME_USER: no fallback ever.
        Set False only for local/dev/simulator callers that construct
        this harness directly and explicitly accept an unbound cassette.
        """

        self.config = config
        self.require_cassette_binding = require_cassette_binding

        # Governing cassette: injected, or loaded for the configured
        # domain. Validated here so an invalid policy halts construction.
        self.cassette = cassette or CassetteLoader().load_cassette(
            config.get("cassette_domain", "ivr")
        )
        validate_cassette(self.cassette)
        # This pipeline reads long_wait_threshold + Twilio ingest
        # (telephony), routes intent through SentinelCore (routing),
        # and clamps healing inside expected_wait_bounds
        # (self_healing). Refuse a cassette missing any of those at
        # construction -- and again at swap -- rather than mid-call.
        from cassette_capabilities import (
            CAPABILITY_ROUTING_TOPOLOGY,
            CAPABILITY_SELF_HEALING,
            CAPABILITY_TELEPHONY_INGEST,
            require_capabilities,
        )
        require_capabilities(
            self.cassette,
            (CAPABILITY_TELEPHONY_INGEST, CAPABILITY_ROUTING_TOPOLOGY,
             CAPABILITY_SELF_HEALING),
            consumer="IcebergProductionHarness",
        )

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

        # Roadster phase-1 (F-A): per-resource breakers. Two separate
        # instances -- a struggling Postgres connection must never gate
        # the Claude governor call, or vice versa. Always constructed
        # (cheap, no I/O); only exercised when the corresponding client
        # below is actually configured.
        self.claude_breaker = CircuitBreaker(
            name="claude_governor", failure_threshold=5, reset_timeout_s=30,
        )
        self.ledger_breaker = CircuitBreaker(
            name="postgres_ledger", failure_threshold=3, reset_timeout_s=15,
        )

        # Catches a different failure mode than claude_breaker above: a
        # syntactically successful, correctly-parsed governor response
        # that repeats a PRIOR call's exact reasoning text verbatim --
        # not an API/transport error (claude_breaker would never see
        # this), a plausible signal of a stuck or degenerate response
        # instead. See governance_loop_guard.py for provenance.
        self.claude_loop_guard = PipelineStateEngine()

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
                if self.require_cassette_binding:
                    # Fail-closed: binding is mandatory and needs a live
                    # ledger. A soft "not available" here would let the
                    # harness continue and govern real decisions with an
                    # unbound cassette -- exactly the gap this item closes.
                    raise RuntimeError(
                        f"PostgreSQL ledger unavailable and "
                        f"require_cassette_binding=True: cannot bind "
                        f"cassette without a ledger. Original error: {e}"
                    ) from e
                print(f"⚠ PostgreSQL not available: {e}")
                self.ledger = None
        elif self.require_cassette_binding:
            raise RuntimeError(
                "require_cassette_binding=True but no postgres_host "
                "configured: cannot bind cassette without a ledger. "
                "Set postgres_host, or pass require_cassette_binding=False "
                "for a dev/simulator harness that accepts an unbound "
                "cassette."
            )

        # Item 2: content-bind the loaded cassette into the ledger chain,
        # fail-closed, before any decision can be governed by it. Only
        # reachable here if either binding was required (ledger is
        # guaranteed present by the checks above) or binding was not
        # required but a ledger happens to be configured anyway -- in
        # both cases, binding an available ledger is strictly better
        # than not.
        if self.ledger is not None:
            cassette_snapshot = serialize_cassette_for_ledger(self._params())
            cassette_hash = compute_cassette_hash(cassette_snapshot)
            cassette_code_hash = _safe_code_hash(self.cassette)
            self.ledger.bind_cassette_version(
                cassette_snapshot.get("cassette_version"),
                cassette_hash,
                cassette_code_hash=cassette_code_hash,
                authorized_by=self.config.get("authorized_by"),
            )
            print(f"✓ Cassette bound: {cassette_snapshot.get('cassette_version')}")
        
        # Claude API
        if self.config.get("claude_api_key"):
            try:
                self.claude_decider = ClaudeGovernanceDecider(
                    api_key=self.config.get("claude_api_key"),
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
        from cassette_capabilities import (
            CAPABILITY_ROUTING_TOPOLOGY,
            CAPABILITY_SELF_HEALING,
            CAPABILITY_TELEPHONY_INGEST,
            require_capabilities,
        )
        require_capabilities(
            cassette,
            (CAPABILITY_TELEPHONY_INGEST, CAPABILITY_ROUTING_TOPOLOGY,
             CAPABILITY_SELF_HEALING),
            consumer="IcebergProductionHarness.swap_cassette",
        )
        self.cassette = cassette
        self.sentinel = SentinelCore(cassette)
        self.twilio_parser.cassette = cassette

    # ---- OutcomeV1 / EventV1: the live path, stamped -------------------------
    #
    # Everything below exists to stop the harness handing the kernel numbers
    # whose origin it has forgotten. By default the route still comes from
    # twilio_log_ingestion's phone-digit rule and the per-node waits still
    # come from its fixed 0.1/0.5/0.4 split -- that default does not fix
    # either one, and is not pretending to. What changes is that both travel
    # with an honest stamp: ESTIMATED with the derivation named when the
    # ingest layer had to guess, VERIFIED naming the real source when the
    # Twilio record carried real per-node events (see twilio_log_ingestion's
    # ivr_events contract). The harness does not decide which happened for
    # a given call -- it reads whatever twilio_log_ingestion already
    # determined and stamps that, honestly, rather than assuming.

    _EMOTION_METHOD = "observe_perceive_core.get_emotional_state: inferred from friction"
    _ORIGIN_METHOD = ("call start derived as (ingest time - total duration); Twilio "
                      "log ingest carries no absolute start timestamp")

    def _assemble_live_episode(self, journey, first_queue, friction_count,
                               friction_events, emotion, measured_waits,
                               call_sid, twilio_record):
        """Build a validated Episode from stamped events for one live call.

        This is the seam that makes the governance kernel real. Until now every
        production caller of make_episode/judge_episode was a test; the live
        path scored calls through a parallel route and the kernel judged
        nothing that had happened.
        """
        observed_at = time.time()
        # The pipeline speaks EmotionalState objects, cassettes speak dicts.
        # SentinelCore already owns that boundary conversion; calling it rather
        # than writing a second one keeps this from becoming the two-places-
        # that-can-quietly-disagree problem the cassette system exists to end.
        emotion_dict = self.sentinel._emotion_as_dict(emotion)
        duration = float(journey.total_duration or 0.0)
        started_at = max(observed_at - duration, 1.0)
        base = f"{call_sid or journey.caller_id}"

        route_provenance = getattr(journey, "route_provenance", PROVENANCE_ESTIMATED)
        route_method = getattr(journey, "route_method", FALLBACK_ROUTE_METHOD)
        wait_provenance = getattr(journey, "wait_provenance", PROVENANCE_ESTIMATED)
        wait_method = getattr(journey, "wait_method", FALLBACK_WAIT_METHOD)

        # event_v1's own integrity rule: a VERIFIED event cannot carry a
        # `method` -- method names how something was DERIVED, and a
        # verified fact was observed, not derived (see event_v1.validate_
        # event). When real ivr_events produced this journey, the real
        # system's name still has to reach the ledger -- it goes in
        # `detail` instead, which carries no such restriction.
        route_is_verified = route_provenance == PROVENANCE_VERIFIED
        wait_is_verified = wait_provenance == PROVENANCE_VERIFIED

        route_detail = {"journey": list(journey.journey),
                         "origin_note": self._ORIGIN_METHOD}
        if route_is_verified:
            route_detail["real_source"] = route_method

        events = [
            make_event(
                event_id=f"{base}:route", episode_id=base, domain="ivr",
                kind="route_selected", occurred_at=started_at,
                observed_at=observed_at, source="twilio_log_ingestion",
                provenance=route_provenance,
                method=None if route_is_verified else route_method,
                fields={"route": first_queue}, detail=route_detail),
        ]
        for node, wait in sorted((measured_waits or {}).items()):
            wait_detail = {"node": node}
            if wait_is_verified:
                wait_detail["real_source"] = wait_method
            events.append(make_event(
                event_id=f"{base}:wait:{node}", episode_id=base, domain="ivr",
                kind="wait_observed",
                occurred_at=min(started_at + float(wait), observed_at),
                observed_at=observed_at, source="twilio_log_ingestion",
                provenance=wait_provenance,
                method=None if wait_is_verified else wait_method,
                fields={f"wait_{node}": float(wait)}, detail=wait_detail))
        events.append(make_event(
            event_id=f"{base}:emotion", episode_id=base, domain="ivr",
            kind="emotion_inferred", occurred_at=observed_at,
            observed_at=observed_at, source="observe_perceive_core",
            provenance=PROVENANCE_ESTIMATED, method=self._EMOTION_METHOD,
            fields={"emotion_frustration": emotion_dict.get("frustration")},
            detail={"emotion": emotion_dict}))
        # The only two facts here that Twilio actually reports rather than
        # something inferring: the call's final status and its duration.
        events.append(make_event(
            event_id=f"{base}:ended", episode_id=base, domain="ivr",
            kind="call_ended", occurred_at=observed_at, observed_at=observed_at,
            source="twilio:call_log", provenance=PROVENANCE_VERIFIED,
            fields={"resolved": bool(journey.resolved), "duration": duration},
            detail={"status": twilio_record.get("status")}))

        reasons = ()
        if not journey.resolved:
            reasons = (f"call ended unresolved (twilio status="
                       f"{twilio_record.get('status')!r}, friction_count="
                       f"{friction_count})",)
        return assemble_episode(
            episode_id=base, domain="ivr",
            requested={"resolved": True},
            events=events,
            outcome_reasons=reasons,
            attributes={
                "duration": duration,
                "friction_count": int(friction_count),
                "emotion": emotion_dict,
                "journey": list(journey.journey),
                "friction_events": list(friction_events or []),
            })

    def _outcome_obligation_declaration(self):
        """The maturation rule this cassette declares, or None.

        None is the honest answer for IVR and every other domain whose outcome
        is settled when the interaction ends -- see cassette_capabilities'
        anti-placeholder rule. A domain that does not enable the capability is
        not asked to invent a horizon."""
        try:
            if CAPABILITY_OUTCOME_OBLIGATION not in self.cassette.capabilities():
                return None
            return self.cassette.get_maturation_rule().declaration()
        except Exception as exc:
            logger.warning("cassette declares outcome_obligation but its maturation "
                           "rule could not be read; no obligation recorded",
                           extra={"extra_data": {"error": str(exc)}})
            return None

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
            obligation_declaration = self._outcome_obligation_declaration()

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
            # Role-tagged first (a real event source's own say-so), name
            # convention only as the fallback when nothing was tagged --
            # see twilio_log_ingestion's NODE ROLES section.
            node_roles = getattr(journey, "node_roles", {}) or {}
            first_queue = next(
                (n for n in journey.journey if node_roles.get(n) == NODE_ROLE_QUEUE),
                None)
            if first_queue is None:
                first_queue = next((n for n in journey.journey if "queue" in n),
                                   "general_queue")
            intent_signal = self.sentinel.infer_intent(journey.journey, first_queue)
            quality_score = self.sentinel.score_outcome_quality(
                journey.resolved, journey.total_duration,
                friction_count, emotion
            )

            # 4b. Kernel: judge the episode. THE governance path, live.
            #
            # Additive on purpose. quality_score above stays the value this
            # harness acts on, and the kernel's verdict is recorded ALONGSIDE
            # it rather than replacing it -- swapping the live scoring path in
            # the same change that introduces the episode would make any
            # difference in behaviour impossible to attribute. Instead the two
            # are cross-checked, and a disagreement becomes an observable
            # ledger fact rather than an assumption nobody tested. The IVR
            # cassette's judge() and score_outcome_quality() are documented as
            # arithmetically identical; this is what continuously proves it.
            kernel = {"judged": False}
            with tracer.start_as_current_span("kernel_judgment") as kernel_span:
                try:
                    assembly = self._assemble_live_episode(
                        journey, first_queue, friction_count, friction_events,
                        emotion, measured_waits, call_sid, twilio_record)
                    kernel_result = judge_episode(self.cassette, assembly.episode)
                    legacy_tier = quality_score.quality_tier.value
                    agrees = str(kernel_result.tier).lower() == str(legacy_tier).lower()
                    kernel = {
                        "judged": True,
                        "tier": kernel_result.tier,
                        "score": round(float(kernel_result.score), 6),
                        "agrees_with_legacy_scoring": agrees,
                        "estimated_fields": list(estimated_fields(assembly.episode)),
                        "field_provenance": assembly.provenance,
                        "source_events": list(assembly.source_events),
                    }
                    kernel_span.set_attribute("kernel.tier", str(kernel_result.tier))
                    kernel_span.set_attribute("kernel.agrees", agrees)
                    if not agrees:
                        # Not swallowed and not fatal: the harness keeps acting
                        # on the legacy score, and the divergence is on the
                        # record for someone to go look at.
                        logger.warning(
                            "kernel judgment disagrees with legacy scoring",
                            extra={"extra_data": {
                                "call_sid": call_sid,
                                "kernel_tier": kernel_result.tier,
                                "legacy_tier": legacy_tier}})
                except (EpisodeIntegrityError, EventIntegrityError, KeyError) as e:
                    # A malformed episode is a real finding -- it means the
                    # ingest path produced something the kernel cannot accept.
                    # It does not take the call down, because the harness has a
                    # working scoring path that does not depend on it; but it
                    # is recorded, never silently dropped.
                    kernel = {"judged": False, "error": type(e).__name__,
                              "detail": str(e)[:400]}
                    kernel_span.set_attribute("kernel.judged", False)
                    mark_error(kernel_span, f"Kernel judgment unavailable: {e}")
                    logger.warning(
                        "kernel could not judge this call; legacy scoring stands",
                        extra={"extra_data": {"call_sid": call_sid,
                                              "error": str(e)[:400]}})

            # 5. Record metrics
            self.metrics.record_call(
                wait_time=journey.total_duration * 0.3,
                resolved=journey.resolved,
                resolution_time=journey.total_duration
            )

            if friction_count > 0:
                self.metrics.record_drift_detection(first_queue, 0.2)

            # 6. Bayes: Update intent success rates
            #
            # Previously passed intent_signal.queue_chosen (e.g.
            # "billing_queue"), but BayesianIntentEngine.intent_stats is
            # keyed by the cassette's short intent labels (e.g. "billing").
            # observe_outcome() silently no-ops on an unrecognized key, so
            # every single observation was dropped -- the belief state
            # never updated no matter how many calls ran. classification
            # is the cassette-native label ("BILLING", "UNKNOWN", ...);
            # lowercased it matches intent_stats exactly for every mapped
            # queue.
            self.bayes.observe_outcome(
                intent_signal.classification.lower(),
                journey.resolved,
                journey.total_duration
            )

            # 7. Governance gate
            claude_decision = None

            # Gate on friction_count alone -- the measured value from
            # step 2, which is also what's handed to the governor
            # (below) and written to the ledger. This used to gate on
            # max(friction_count, journey.friction_count), mixing the
            # measured value with twilio_log_ingestion's separate
            # ingest-side ESTIMATE (different heuristic, different
            # thresholds -- see _count_friction's own docstring, which
            # already states "the production harness does NOT use this
            # estimate on the governance path"; the max() contradicted
            # that). Confirmed live with an in-bounds retuned cassette:
            # the gate could govern and approve a call while the
            # governor and the ledger both saw friction_count=0 against
            # governance_trigger=2 -- a row that couldn't reproduce its
            # own decision. On the shipped default cassette this never
            # changed the outcome (measured >= ingest in every governed
            # case), so behavior here is unchanged for existing traffic.
            governed = friction_count >= governance_trigger
            root_span.set_attribute("call.governed", governed)
            root_span.set_attribute("call.queue", first_queue)
            root_span.set_attribute("call.friction_count", friction_count)

            if self.claude_decider and governed:
                with tracer.start_as_current_span("governance_decision") as gov_span:
                    try:
                        claude_decision = self.claude_breaker.call(
                            self.claude_decider.safety_check,
                            "heal_queue",
                            {
                                "queue": first_queue,
                                "wait_time": journey.total_duration,
                                "friction_count": friction_count
                            },
                            # safety_check() fails closed by RETURNING a
                            # dict, never raising (see its own
                            # docstring) -- an exception-only breaker
                            # would never see these. Only the
                            # "transport_error:"-prefixed reasoning is a
                            # real API/network failure worth tripping
                            # the breaker on; a malformed-JSON or
                            # bad-shape response from a live, reachable
                            # API is not an outage and must not count.
                            is_failure=lambda r: isinstance(r, dict) and str(
                                r.get("reasoning", "")
                            ).startswith("transport_error:"),
                        )

                        # Loop guard: only meaningful for a response that
                        # actually reached and was parsed by a real model.
                        # model_identity is None on every fail-closed path
                        # (no client configured, JSON parse failure,
                        # transport error) -- see claude_governance_api.py,
                        # every one of those return dicts sets it to None
                        # explicitly. Skipping those avoids false-tripping
                        # the loop guard on their fixed, always-repeating
                        # reasoning text (e.g. "No API client configured"),
                        # which isn't a governor loop, it's the same
                        # non-decision every time by design.
                        if claude_decision.get("model_identity"):
                            loop_state = self.claude_loop_guard.process_lifecycle(
                                claude_decision.get("reasoning", "")
                            )
                            if loop_state == "BLOCKED_LOOP":
                                gov_span.set_attribute("decision.loop_blocked", True)
                                claude_decision = {
                                    "safe": False,
                                    "governed": False,
                                    "parse_failed": False,
                                    "reasoning": (
                                        "Governor returned reasoning identical to a "
                                        "prior call -- possible stuck/degenerate "
                                        "response, blocked by the loop guard"
                                    ),
                                    "confidence": 0.0,
                                }

                        gov_span.set_attribute("decision.approved", bool(claude_decision.get("safe")))
                    except Exception as e:
                        # CircuitOpenError (breaker OPEN) lands here too --
                        # same fail-closed shape as any other governor
                        # exception, no new branch needed.
                        print(f"Claude decision failed: {e}")
                        gov_span.record_exception(e)
                        mark_error(gov_span, f"Governor exception: {e}")
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
            ledger_write_failed = False
            if self.ledger and claude_decision is not None:
                with tracer.start_as_current_span("ledger_write") as ledger_span:
                    try:
                        approved = bool(claude_decision.get("safe"))
                        root_span.set_attribute("call.approved", approved)
                        self.ledger_breaker.call(
                            self.ledger.append_decision,
                            GovernanceDecisionRecord(
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
                                    # EventV1: which of the numbers above were
                                    # measured and which were derived. This is
                                    # what an auditor asks for when they want
                                    # to know how much of a record is real.
                                    "kernel": kernel,
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
                                # Item 3: hash of the cassette DECISION CODE
                                # (scoring/intent logic), not just its
                                # parameters. Computed from the live cassette
                                # object so it enters the canonical hash. Closes
                                # F-H: a params-identical cassette with changed
                                # logic no longer hashes identically.
                                cassette_code_hash=_safe_code_hash(self.cassette),
                                # Item 5: the model the governor actually used
                                # (response.model), passed through from
                                # safety_check so it enters the canonical hash.
                                # None on any fail-closed governor path.
                                model_identity=claude_decision.get("model_identity"),
                                # Item 7: the authorizing service identity. This
                                # is a role/key NAME, never a raw key and never
                                # PII. Defaults to the harness service identity;
                                # override via config for distinct deployments.
                                authorized_by=self.config.get(
                                    "authorized_by", "harness:production"),
                                # OutcomeV1: the maturation rule in force at
                                # decision time, hashed in now and never
                                # edited. The obligation record points back at
                                # this row; this row points at nothing, which
                                # is what lets it close permanently.
                                outcome_obligation=obligation_declaration,
                                # Item 8 (2026-07-31): real usage-derived cost
                                # of this safety_check call, passed through
                                # from claude_governance_api unchanged -- see
                                # ai_cost_tracking.py. None on any fail-closed
                                # governor path (no API call, nothing to cost).
                                ai_cost=claude_decision.get("cost"),
                            ),
                            governance_params=params,
                        )
                    except Exception as e:
                        # CircuitOpenError (breaker OPEN) lands here too --
                        # same ledger_write_failed=True / retryable shape
                        # sentinel_worker.py already fails correctly on.
                        # A decision that isn't durably recorded is not an
                        # audited decision. This used to be a print() with
                        # the function falling through to report
                        # governance_approved straight from claude_decision
                        # -- a caller could be told "approved" with no row
                        # to show for it. Confirmed live under concurrent
                        # duplicate-sid submissions at realistic governor
                        # latency: 3 of 4 approvals went unrecorded and
                        # were still reported as approved.
                        #
                        # This is returned in the response below, not
                        # raised: process_call runs under
                        # ResilientHarness's retry_with_backoff, which
                        # retries ANY exception up to 3x and opens the
                        # circuit breaker after 5 failures -- raising here
                        # would re-invoke the governor on every retry for
                        # what is often a transient write failure, and
                        # could trip the breaker on a brief blip.
                        ledger_write_failed = True
                        logger.error(
                            "Ledger write failed for a governed decision -- "
                            "decision was NOT durably recorded",
                            extra={"extra_data": {
                                "call_sid": call_sid,
                                "node": first_queue,
                                "claude_safe": claude_decision.get("safe"),
                                "error": str(e),
                            }},
                        )
                        ledger_span.record_exception(e)
                        ledger_span.set_attribute("ledger_write.failed", True)
                        mark_error(ledger_span, f"Ledger write failed: {e}")

            # A call only counts as approved if the approval is both
            # granted AND durably recorded; either half missing means the
            # call was not successfully governed end-to-end.
            governance_approved = (
                bool(claude_decision.get("safe", False))
                if claude_decision is not None and not ledger_write_failed
                else False
            )
            governance_blocked = governed and (
                claude_decision is None or
                not claude_decision.get("safe", False) or
                ledger_write_failed
            )

            return {
                "caller_id": journey.caller_id,
                "resolved": journey.resolved,
                "quality": quality_score.quality_tier.value,
                "intent": intent_signal.queue_chosen,
                "intent_classification": intent_signal.classification,
                "emotion_frustration": emotion.frustration,
                "claude_safe": claude_decision.get("safe") if claude_decision else None,
                "governance_required": governed,
                "governance_approved": governance_approved,
                "governance_blocked": governance_blocked,
                "ledger_write_failed": ledger_write_failed,
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
        # calls_total from get_summary() is self.metrics.calls_total -- a
        # cumulative, process-lifetime counter (correct for /metrics, which
        # reports the harness's running totals). Reusing it here as a
        # per-batch figure is wrong: it silently includes every call this
        # harness instance has ever processed, not just this batch, so it
        # drifts from calls_processed (len(results), correctly batch-scoped)
        # by however many calls preceded this batch in the harness's
        # lifetime. Override it to the batch-local count so both fields
        # describe the same scope.
        summary["calls_total"] = len(results)
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
