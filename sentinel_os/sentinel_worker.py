"""sentinel_worker.py -- the piece that actually does the work the
transmission queue was built to protect.

One worker = one long-lived harness (cassette loaded once, Postgres pool
held open, Claude client held open if configured) pulling jobs off
TransmissionQueue in a loop. Multiple worker processes run side by side;
the queue's claim fencing is what makes that safe.

HARNESS (2026-08-07): re-pointed from IcebergProductionHarness onto
GovernanceHarness (governance_harness.py), the domain-agnostic kernel
harness -- swap_cassette() covers both the fixed-per-client case and a
future multi-tenant one without redoing this migration later.
GovernanceHarness refuses any cassette declaring CAPABILITY_TELEPHONY_
INGEST (the inverse of IcebergProductionHarness's own posture), so this
worker no longer governs Twilio call traffic -- it governs
MortgageCassette decisions instead (see main()). SentinelWorker's own
code below is UNCHANGED: it only ever calls
self.harness.process_call(job.payload), and GovernanceHarnessJobAdapter
(below) is what now sits behind that call, translating a queued job
payload into (Episode, issue_count) for GovernanceHarness.process() and
translating the result back into the exact process_call(payload)->dict
shape this file's ack/fail branching already depends on. See that
class's own docstring for the redelivery-dedup gap it closes --
GovernanceHarness has no equivalent to IcebergProductionHarness's
sid_exists() pre-check, so the adapter (via the new
PostgreSQLLedger.episode_decision_exists()) builds one.
production_harness.py/IcebergProductionHarness itself, and its other
consumers (resilient_harness.py, cassette_harness.py, the circuit-
breaker/production-harness test suites), are untouched -- they keep
running exactly as before.

BIAS/FAIR-LENDING SCREENING (2026-08-07): main() now inserts the CFPB
Reg B reference lens into a RegulatoryDeck, LIVE mode, flag-only, and
hands it to GovernanceHarness via the new regulatory_deck attribute
(governance_harness.py). See the comment at the insertion site for
exact scope. GovernanceHarness itself stays domain-agnostic --
regulatory_deck is optional and defaults to None, so every other
caller (tests, any future non-mortgage cassette on this harness) is
byte-identical to before this was added.

THE CENTRAL DESIGN DECISION: how a claimed job's outcome maps to
ack/fail is not "did process_call raise" -- it mostly doesn't, by
design (see production_harness.py's own comments on why ledger-write
failure is returned, not raised). It's decided from the *shape* of what
process_call returns, because that shape already encodes the exact
distinction this project has been burned by twice:

  duplicate_sid           -> ACK.  Not a new failure -- proof the
                              at-least-once/dedup contract worked. This
                              is precisely the crash-between-commit-
                              and-ack path the queue's docstring
                              promises is safe: a worker died after
                              committing to the ledger but before
                              acking, the job got redelivered, and the
                              ledger's own sid dedup caught it. Acking
                              here is what makes that promise true
                              rather than aspirational.
  parse failure            -> FAIL, data_corruption, non-retryable.
                              Bad input won't heal by retrying it.
  ledger_write_failed=True -> FAIL, retryable. THIS IS THE F-2 SHAPE:
                              a decision was made but not durably
                              recorded. Acking it would silently lose
                              the audit row all over again -- the one
                              outcome this whole system exists to
                              prevent. Never ack this branch.
  anything else            -> ACK. Includes governance_blocked=True:
                              a call the governor correctly rejected,
                              and durably recorded as rejected, is a
                              SUCCESSFULLY processed job. Only ledger
                              failure or bad input make a job a queue
                              failure -- a "no" from the governor is a
                              legitimate, complete outcome.
  harness raises            -> FAIL, reason from
                              TransmissionQueue.classify_exception().
                              Defensive: the harness's own contract is
                              to catch and report, not raise, but a
                              worker must never treat "I don't know
                              what happened" as success.

Reaping (crash recovery for OTHER workers' abandoned leases) runs on a
timer in a background thread per worker, not only on the worker's own
idle moments -- so recovery keeps happening even while every worker is
saturated with claimed jobs.

HEARTBEAT (2026-07-31): queue_schema.py's heartbeat() -- lease renewal
grafted in from the rebuild, per its own docstring the original v1 engine
"had NO lease renewal -- a lease could only expire" -- existed but was
never actually called anywhere in this file. A worker claimed a job with
the queue's default 30-second lease, then called process_call (a real
Claude API call, a Postgres write, and as of this session's own cohort-
equity-escalation work, sometimes a real network call to the twin) with
nothing renewing that lease while it ran. A slow call could outlive its
lease and get reaped and redelivered to a different worker while the
first worker was still legitimately working on it -- not a correctness
bug (the ledger's sid dedup already makes redelivery safe, same as the
crash-recovery path above; STALE from a lost heartbeat is handled the
same fenced way ack/fail already are, per queue_schema.py's own Outcome
docstring), but wasted work and a spurious retry/backoff. Fixed by having
the SAME reaper timer also heartbeat whichever job this worker currently
holds -- see start_reaper()'s docstring for why that reuses the existing
thread instead of spawning a new one per job.
"""
from __future__ import annotations

import argparse
import os
import signal
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

from cassettes.mortgage_cassette import MortgageCassette
from episode import (
    Episode,
    EpisodeIntegrityError,
    make_episode,
    outcome_mismatches,
    validate_episode,
)
from governance_harness import GovernanceHarness
from operational_resilience import setup_logging

from queue_schema import ClaimedJob, Outcome, Reason, TransmissionQueue

logger = setup_logging("SentinelWorker")


def _harness_config_from_env() -> dict:
    return {
        "postgres_host": os.getenv("POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", 5432)),
        "postgres_db": os.getenv("POSTGRES_DB", "iceberg"),
        "postgres_user": os.getenv("POSTGRES_USER", "iceberg"),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", "iceberg"),
        "claude_api_key": os.getenv("CLAUDE_API_KEY"),
        # No cassette_domain here (IcebergProductionHarness's old config-
        # driven cassette lookup) -- GovernanceHarness takes a cassette
        # OBJECT directly (see main()), so a domain string has nothing
        # to select among and would be misleading to leave in.
    }


# ---------------------------------------------------------------------------
# GovernanceHarness adapter -- turns a queued job.payload into
# (Episode, issue_count) and translates GovernanceHarness.process()'s
# result back into the SAME process_call(payload)->dict shape
# IcebergProductionHarness's already produces, so SentinelWorker.handle_one
# (below) needs NO changes: it only ever calls self.harness.process_call(...)
# and branches on the shape of what comes back.
# ---------------------------------------------------------------------------

def _payload_to_mortgage_episode(cassette: MortgageCassette, payload: Dict) -> Tuple[Episode, int]:
    """Turn one queued job payload into (Episode, issue_count) for
    GovernanceHarness.process() -- mortgage-domain-shaped, matching
    MortgageCassette's own vocabulary (see cassettes/mortgage_cassette.py).

    Payload shape:
      episode_id      -- required, non-empty string. Also the dedup key
                         PostgreSQLLedger.episode_decision_exists() checks
                         before this episode is processed again.
      requested       -- required dict: what was asked for.
      actual          -- required dict: what was observed.
      actor_report    -- optional dict, defaults to {}.
      outcome_reasons -- optional list of str, defaults to [].
      attributes      -- optional dict, defaults to {} (e.g. carries
                         mortgage_cassette.PROPERTY_ADDRESS_FIELD).

    issue_count follows mortgage_cassette.py's OWN governance_trigger
    parameter description verbatim, not a rule invented here: "Episodes
    with >= this many decision-process integrity issues (requested-vs-
    actual mismatches, or an adverse action recorded with a reason too
    thin to be a documented basis)". Reads the cassette's own
    _THIN_REASON_WORD_COUNT rather than a duplicated literal, so this
    can never drift from judge()'s own reason-substance check.

    Raises ValueError/TypeError on a structurally malformed payload
    (missing or wrong-typed required fields) or EpisodeIntegrityError on
    a kernel-invalid episode (e.g. a mismatch with no outcome reason on
    file) -- both are caught by GovernanceHarnessJobAdapter.process_call
    and reported as the same {"error": ...} shape IcebergProductionHarness's
    own parse-failure path already uses.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"job payload must be a dict, got {type(payload).__name__}")
    episode_id = payload.get("episode_id")
    if not episode_id or not isinstance(episode_id, str):
        raise ValueError("job payload missing required non-empty string 'episode_id'")
    requested = payload.get("requested")
    actual = payload.get("actual")
    if not isinstance(requested, dict):
        raise ValueError(f"job payload 'requested' must be a dict, got {type(requested).__name__}")
    if not isinstance(actual, dict):
        raise ValueError(f"job payload 'actual' must be a dict, got {type(actual).__name__}")

    episode = make_episode(
        episode_id=episode_id,
        domain=cassette.get_config().domain,
        requested=requested,
        actual=actual,
        actor_report=payload.get("actor_report"),
        outcome_reasons=payload.get("outcome_reasons") or (),
        attributes=payload.get("attributes"),
    )
    validate_episode(episode)  # raises EpisodeIntegrityError on a real kernel violation

    mismatch_count = len(outcome_mismatches(episode))
    thin_reason_count = sum(
        1 for r in episode.outcome_reasons
        if len(r.split()) < cassette._THIN_REASON_WORD_COUNT
    )
    issue_count = mismatch_count + thin_reason_count
    return episode, issue_count


class GovernanceHarnessJobAdapter:
    """Adapts a GovernanceHarness (episode-shaped: process(episode,
    issue_count)) to the process_call(payload) -> dict interface
    SentinelWorker.handle_one already depends on. SentinelWorker itself
    needs no changes -- only what gets constructed and handed to it as
    `harness` changes (see main()).

    Reuses the "duplicate_sid" error string verbatim (not a new
    sentinel) so handle_one's existing `if error == "duplicate_sid":`
    branch fires unchanged for a redelivered, already-recorded episode
    -- what handle_one's branching depends on is the KEY NAMES and
    VALUES in the returned dict, not any telephony-specific meaning
    behind them.

    THE DEDUP GAP THIS CLOSES: GovernanceHarness.process() has no
    equivalent to IcebergProductionHarness's sid_exists() pre-check --
    confirmed by reading PostgreSQLLedger.append_decision() in full,
    which validates required fields and appends, nothing else. Without
    a pre-check here, a redelivered job (crash before ack, a lapsed
    heartbeat) would write a SECOND governance_decision ledger row
    instead of being recognized as already-done -- exactly the
    duplicate-write hazard this whole system exists to prevent, now
    unguarded. episode_decision_exists() (governance/ledger_postgres.py)
    is the new, minimal, sid_exists-mirroring lookup this pre-check
    needs; it did not exist before this change.
    """

    def __init__(self, harness: GovernanceHarness):
        self.harness = harness

    def process_call(self, payload: Dict) -> Dict:
        try:
            episode, issue_count = _payload_to_mortgage_episode(self.harness.cassette, payload)
        except (ValueError, TypeError, EpisodeIntegrityError) as exc:
            return {"error": f"Failed to parse job: {exc}"}

        if (self.harness.ledger is not None
                and self.harness.ledger.episode_decision_exists(episode.episode_id)):
            return {
                "error": "duplicate_sid",
                "detail": f"Episode {episode.episode_id} has already been processed",
                "sid": episode.episode_id,
            }

        try:
            result = self.harness.process(episode, issue_count)
        except Exception:
            # GovernanceHarness.process() does not catch a ledger-write
            # failure the way IcebergProductionHarness's process_call
            # does (see that method's own comments on why it's returned,
            # not raised) -- it raises straight out of _write_decision.
            # Translate to the SAME ledger_write_failed=True dict shape
            # here so handle_one's existing branch (fail, retryable,
            # NEVER ack) fires exactly as it does for
            # IcebergProductionHarness. The governor decision that
            # preceded the failed write (real cost if a real API call
            # was made) is lost either way -- same F-2 shape, delivered
            # via exception here instead of a dict flag.
            return {
                "error": None,
                "ledger_write_failed": True,
                # Approximate log-readability analogs of IVR's
                # claude_safe/intent fields (handle_one only ever
                # interpolates these into a log message, never branches
                # on them) -- not a semantic requirement, just kept
                # meaningful rather than always None.
                "claude_safe": None,
                "intent": episode.domain,
            }

        return {
            "error": None,
            "ledger_write_failed": False,
            "governed": result.get("governed", False),
            "governance_approved": result.get("governance_approved", False),
            "governance_blocked": result.get("governance_blocked", False),
            "claude_safe": result.get("governance_approved"),
            "intent": episode.domain,
        }

    def shutdown(self) -> None:
        self.harness.shutdown()


class SentinelWorker:
    """Wraps one harness with a claim/process/ack-or-fail loop against
    the transmission queue, plus a background reaper.

    `harness` is duck-typed, not a fixed class -- anything with
    process_call(payload) -> dict and shutdown() works (see
    GovernanceHarnessJobAdapter above for the current production
    wiring; a bare IcebergProductionHarness satisfies the same shape
    directly and still works here unmodified, for anything that
    constructs a SentinelWorker with one directly)."""

    def __init__(
        self,
        harness: Any,
        queue: TransmissionQueue,
        *,
        worker_id: Optional[str] = None,
        claim_wait_s: float = 1.0,
        reap_interval_s: float = 5.0,
        idle_log_every: int = 200,
    ) -> None:
        self.harness = harness
        self.queue = queue
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:10]}"
        self.claim_wait_s = claim_wait_s
        self.reap_interval_s = reap_interval_s
        self.idle_log_every = idle_log_every
        self._stop = threading.Event()
        self._reaper_thread: Optional[threading.Thread] = None
        # The job (if any) this worker is currently inside process_call for
        # -- set/cleared only by handle_one, on the claim/dispatch thread.
        # Read by the reaper thread every tick to decide whether there's
        # anything to heartbeat. A single-writer reference swap like this
        # is safe to read from another thread without a lock under
        # CPython's GIL -- the reader always sees a complete ClaimedJob or
        # None, never a torn value.
        self._current_job: Optional[ClaimedJob] = None
        self.processed = 0
        self.acked = 0
        self.failed = 0

    # ------------------------------------------------------- lifecycle --
    def start_reaper(self) -> None:
        """Background thread: recovers ANY worker's abandoned leases,
        not just this one's, on a fixed timer independent of this
        worker's claim loop.

        This same timer also heartbeats THIS worker's own in-flight job
        (self._current_job), if it has one -- see module docstring's
        HEARTBEAT section. Reusing the reaper's existing thread/timer
        instead of spawning a dedicated heartbeat thread per job works
        because a single SentinelWorker only ever processes one job at
        a time (run_forever's claim loop is sequential, not concurrent)
        -- there is never more than one job that could need a heartbeat
        from this worker, so one shared timer covers both jobs with no
        extra thread-per-job overhead. reap_interval_s (default 5s)
        against the queue's default 30s lease leaves several renewals'
        worth of margin before expiry; a caller that wants a shorter
        lease should pass a proportionally shorter reap_interval_s too.
        """

        def _loop():
            while not self._stop.is_set():
                try:
                    report = self.queue.reap_expired()
                    if report["requeued"] or report["dead"] or report["orphaned"]:
                        logger.warning(
                            "Reaped expired leases",
                            extra={"extra_data": {
                                "worker_id": self.worker_id,
                                "requeued": report["requeued"],
                                "dead": report["dead"],
                                "orphaned": report["orphaned"],
                            }},
                        )
                except Exception:
                    logger.exception("Reap sweep failed; will retry next interval")
                job = self._current_job
                if job is not None:
                    try:
                        outcome = self.queue.heartbeat(job)
                        if outcome is not Outcome.OK:
                            # STALE: already reaped/reclaimed by someone
                            # else. GONE: already completed. Either way
                            # this worker no longer owns it -- nothing to
                            # do here, ack()/fail()'s own fence (and the
                            # ledger's sid dedup if it gets reprocessed
                            # elsewhere) already make this safe; logging
                            # is purely so an operator can see it happened.
                            logger.warning(
                                "Heartbeat did not renew the in-flight "
                                "job's lease -- it may already be "
                                "reclaimed by another worker",
                                extra={"extra_data": {
                                    "job_id": job.id,
                                    "worker_id": self.worker_id,
                                    "outcome": outcome.value}},
                            )
                    except Exception:
                        logger.exception(
                            "Heartbeat call raised; will retry next interval")
                self._stop.wait(self.reap_interval_s)

        self._reaper_thread = threading.Thread(
            target=_loop, name=f"{self.worker_id}-reaper", daemon=True
        )
        self._reaper_thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --------------------------------------------------------- one job --
    def handle_one(self, job: ClaimedJob) -> Outcome:
        """Process exactly one claimed job to a terminal ack/fail. Never
        lets an unexpected exception escape without fail()ing the job
        first -- an unhandled exception here would leave the job
        correctly recoverable by lease expiry, but only after a full
        lease timeout instead of immediately."""
        call_sid = job.payload.get("sid", job.id)
        log_ctx = {"job_id": job.id, "call_sid": call_sid,
                  "attempt": job.attempt, "worker_id": self.worker_id}
        self._current_job = job
        try:
            result = self.harness.process_call(job.payload)
        except Exception as exc:
            reason = TransmissionQueue.classify_exception(exc)
            outcome, backoff = self.queue.fail(
                job, reason, f"process_call raised: {exc}"
            )
            logger.error(
                "process_call raised; failing job",
                extra={"extra_data": {**log_ctx, "reason": reason.value,
                                      "outcome": outcome.value,
                                      "backoff_ms": backoff, "error": str(exc)}},
            )
            self.failed += 1
            return outcome
        finally:
            # Only this method (the claim/dispatch thread) ever writes
            # _current_job -- clearing it here, in both the exception and
            # normal-return paths, is what stops the reaper from
            # heartbeating a job this worker is done with.
            self._current_job = None

        error = result.get("error")

        if error == "duplicate_sid":
            # See module docstring: this is the crash-between-commit-
            # and-ack path working as designed, not a new problem.
            outcome = self.queue.ack(job)
            logger.info(
                "Duplicate sid on redelivery -- ledger already holds "
                "this decision; acking as already-done",
                extra={"extra_data": {**log_ctx, "outcome": outcome.value}},
            )
            self.acked += 1
            return outcome

        if error is not None:
            # Any other error shape from process_call so far is a
            # parse/input failure -- the record itself is bad, not the
            # infrastructure around it.
            outcome, backoff = self.queue.fail(
                job, Reason.DATA_CORRUPTION, f"process_call error: {error}",
                retryable=False,
            )
            logger.warning(
                "Job failed input validation; dead-lettering",
                extra={"extra_data": {**log_ctx, "error": error,
                                      "outcome": outcome.value}},
            )
            self.failed += 1
            return outcome

        if result.get("ledger_write_failed"):
            # THE F-2 SHAPE. A governance decision happened but was not
            # durably recorded. Must retry, must never ack.
            outcome, backoff = self.queue.fail(
                job, Reason.DB_CONNECTION_LOSS,
                f"ledger write failed for a governed decision "
                f"(claude_safe={result.get('claude_safe')}, "
                f"node={result.get('intent')}); decision NOT durably "
                f"recorded -- see harness structured logs for call_sid",
            )
            logger.error(
                "LEDGER WRITE FAILED for a governed decision -- retrying, "
                "not acking",
                extra={"extra_data": {**log_ctx, "outcome": outcome.value,
                                      "backoff_ms": backoff}},
            )
            self.failed += 1
            return outcome

        # Success: recorded and complete, whether the governor said yes
        # or no. A correctly-recorded rejection is a finished job.
        outcome = self.queue.ack(job)
        logger.info(
            "Job completed",
            extra={"extra_data": {
                **log_ctx, "outcome": outcome.value,
                "governed": result.get("governed"),
                "governance_approved": result.get("governance_approved"),
                "governance_blocked": result.get("governance_blocked"),
            }},
        )
        self.acked += 1
        return outcome

    # -------------------------------------------------------- run loop --
    def run_forever(self) -> None:
        self.start_reaper()
        idle_streak = 0
        logger.info("Worker starting", extra={"extra_data": {
            "worker_id": self.worker_id, "queue_prefix": self.queue.prefix}})
        try:
            while not self._stop.is_set():
                job = self.queue.claim(
                    self.worker_id, wait_timeout_s=self.claim_wait_s
                )
                if job is None:
                    idle_streak += 1
                    if idle_streak % self.idle_log_every == 0:
                        logger.debug("Idle", extra={"extra_data": {
                            "worker_id": self.worker_id,
                            "idle_polls": idle_streak}})
                    continue
                idle_streak = 0
                self.processed += 1
                self.handle_one(job)
        finally:
            self.stop()
            logger.info("Worker stopped", extra={"extra_data": {
                "worker_id": self.worker_id, "processed": self.processed,
                "acked": self.acked, "failed": self.failed}})


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel OS transmission worker")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--redis-url", default=os.getenv(
        "SENTINEL_REDIS_URL", "redis://localhost:6379/0"))
    # Converter (see api_server_v2.py's matching comment for the full
    # story): SENTINEL_QUEUE_ID is the one identifier both this worker
    # and the ingress read by default, so there is nothing for an
    # operator to keep in sync across two processes by hand.
    # SENTINEL_QUEUE_NAME remains available as an explicit override for
    # anyone who wants this worker on a different queue than the
    # ingress's default derivation would produce -- it takes precedence
    # when set.
    parser.add_argument("--queue-name", default=os.getenv(
        "SENTINEL_QUEUE_NAME", os.getenv("SENTINEL_QUEUE_ID", "v12")))
    args = parser.parse_args()

    # require_cassette_binding is hardcoded True, not read from env --
    # same posture as ICEBERG_LEDGER_RUNTIME_USER: this is the real
    # production entrypoint, and there is no fallback that lets it start
    # ungoverned by an operator forgetting to set a flag.
    #
    # MortgageCassette, fixed at construction (Config A: fixed-per-
    # client). GovernanceHarness.swap_cassette() is available for a
    # future multi-tenant (Config B) wiring without redoing this
    # migration -- not built here, since nothing in this task asked for
    # multi-cassette dispatch.
    governance_harness = GovernanceHarness(
        _harness_config_from_env(), MortgageCassette(), require_cassette_binding=True,
    )
    # Bias/fair-lending screening (2026-08-07): the CFPB/ECOA/Reg B
    # reference lens, LIVE mode, flag-only (block_on_placeholder stays
    # at its default False -- a boilerplate adverse-action reason is
    # disclosed for human review, never blocks the decision; same
    # "always ACTION_FLAG" posture regulatory_deck.py's cohort-equity
    # escalation already established). Covers dimension 1 (declared
    # proxy / prohibited-basis input screening) and the reason-
    # specificity check, both always-on in this lens with no extra
    # infrastructure needed. Deliberately NOT enabled here: the tier
    # and narrative opt-ins (no authorized-tier declarations exist for
    # mortgage inputs yet -- turning those on now would just flag
    # everything as undeclared) and the cohort-level dimensions 4-6
    # (need a twin client plus a scheduled obligation_sweep.py run,
    # neither of which this repo runs anywhere yet -- see
    # COMPLIANCE.md). Only constructed when the harness actually has a
    # ledger (require_cassette_binding=True above means it always will
    # in this real entrypoint; the guard exists so an
    # offline/unbound harness -- tests, dev runs with binding opted
    # out -- never hits RegulatoryDeck's own "no ledgerless mode"
    # refusal).
    if governance_harness.ledger is not None:
        from regulatory_cassette_interface import MODE_LIVE
        from regulatory_cassettes.cfpb_reg_b import CFPBRegBLens
        from regulatory_deck import RegulatoryDeck

        deck = RegulatoryDeck(governance_harness.ledger,
                              default_authorized_by="sentinel_worker:mortgage")
        deck.insert(CFPBRegBLens(), MODE_LIVE,
                   inserted_by="sentinel_worker:mortgage")
        governance_harness.regulatory_deck = deck
    harness = GovernanceHarnessJobAdapter(governance_harness)
    queue = TransmissionQueue(name=args.queue_name, redis_url=args.redis_url)
    worker = SentinelWorker(harness, queue, worker_id=args.worker_id)

    def _handle_signal(signum, _frame):
        logger.info("Received shutdown signal", extra={"extra_data": {
            "signal": signum}})
        worker.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        worker.run_forever()
    finally:
        harness.shutdown()
        queue.close()


if __name__ == "__main__":
    main()
