"""sentinel_worker.py -- the piece that actually does the work the
transmission queue was built to protect.

One worker = one long-lived IcebergProductionHarness (cassette loaded
once, Postgres pool held open, Claude client held open if configured)
pulling jobs off TransmissionQueue in a loop. Multiple worker processes
run side by side; the queue's claim fencing is what makes that safe.

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
"""
from __future__ import annotations

import argparse
import os
import signal
import threading
import uuid
from typing import Optional

from operational_resilience import setup_logging
from production_harness import IcebergProductionHarness

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
        "cassette_domain": os.getenv("SENTINEL_CASSETTE_DOMAIN", "ivr"),
    }


class SentinelWorker:
    """Wraps one IcebergProductionHarness with a claim/process/ack-or-fail
    loop against the transmission, plus a background reaper."""

    def __init__(
        self,
        harness: IcebergProductionHarness,
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
        self.processed = 0
        self.acked = 0
        self.failed = 0

    # ------------------------------------------------------- lifecycle --
    def start_reaper(self) -> None:
        """Background thread: recovers ANY worker's abandoned leases,
        not just this one's, on a fixed timer independent of this
        worker's claim loop."""

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
    harness = IcebergProductionHarness(
        _harness_config_from_env(), require_cassette_binding=True,
    )
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
