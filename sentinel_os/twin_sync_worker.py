"""twin_sync_worker -- delivers sealed sync jobs to replica receivers (DAP-5, deliver side).

Reuses the shipped TransmissionQueue exactly as sentinel_worker does: atomic
claim -> attempt -> fenced ack/fail. No parallel retry machinery, no second
dead-letter concept; a struggling replica backs a queue up, precisely the way
a struggling worker already does.

Transport failures map onto the EXISTING Reason vocabulary (no parallel
taxonomy):

  connect refused / connect timeout   -> SERVICE_INTERRUPTION  (retryable)
  read timeout / partition mid-flight -> NETWORK_LATENCY       (retryable)
  HTTP 5xx from receiver              -> SERVICE_INTERRUPTION  (retryable)
  HTTP 422 structural reject          -> DATA_CORRUPTION       (dead-letter)
  HTTP 409 immutability conflict      -> DATA_CORRUPTION       (dead-letter; a
        delivery that would REWRITE stored history is never retried into place)
  HTTP 401 bad ship token             -> UNCLASSIFIED          (dead-letter,
        operator problem, not a transport state)

Test hook: TWIN_SYNC_CORRUPT_ONCE=1 truncates the first envelope's ciphertext
before sending (forced torn delivery -> 422 -> DATA_CORRUPTION dead-letter ->
operator requeue_from_dlq path).
"""

from __future__ import annotations

import argparse
import base64
import os
import time
import uuid
from typing import Optional

import httpx

from queue_schema import ClaimedJob, Outcome, Reason, TransmissionQueue

SYNC_QUEUE_NAME = "twin_sync"


class TwinSyncWorker:
    def __init__(self, queue: TransmissionQueue, worker_id: Optional[str] = None,
                 request_timeout_s: float = 3.0):
        self.queue = queue
        self.worker_id = worker_id or f"twinsync-{uuid.uuid4().hex[:8]}"
        self.request_timeout_s = request_timeout_s
        self.delivered = 0
        self.failed = 0

    def _post(self, job: ClaimedJob) -> httpx.Response:
        p = job.payload
        entry = {
            "primary_id": p["primary_id"],
            "call_sid": p.get("call_sid"),
            "previous_hash": p["previous_hash"],
            "current_hash": p["current_hash"],
            "envelope": p["envelope"],
        }
        if os.environ.pop("TWIN_SYNC_CORRUPT_ONCE", None):
            env = dict(entry["envelope"])
            # Truncate hard enough that the raw ciphertext drops below the
            # receiver's structural minimum (a full AES-GCM tag is 16 bytes,
            # so anything under 17 raw bytes cannot be a real ciphertext+tag).
            # A proportional truncation (e.g. len//3) is NOT guaranteed to
            # cross that floor for small payloads -- it can decode to a
            # plausible-length-but-wrong-content blob that passes shape
            # validation and gets silently stored, which is not what "torn
            # delivery" is meant to simulate.
            env["ct"] = base64.b64encode(base64.b64decode(env["ct"])[:8]).decode()
            entry["envelope"] = env
        url = f"{p['receiver_url'].rstrip('/')}/replica/{p['replica_id']}/entries"
        return httpx.post(url, json=entry,
                          headers={"Authorization": f"Bearer {p['ship_token']}"},
                          timeout=self.request_timeout_s)

    def handle_one(self, job: ClaimedJob) -> Outcome:
        try:
            resp = self._post(job)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self.failed += 1
            outcome, _ = self.queue.fail(job, Reason.SERVICE_INTERRUPTION,
                                         f"receiver unreachable: {exc}", retryable=True)
            return outcome
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            self.failed += 1
            outcome, _ = self.queue.fail(job, Reason.NETWORK_LATENCY,
                                         f"partition/latency mid-flight: {exc}", retryable=True)
            return outcome

        if resp.status_code == 200:
            body = resp.json()
            self.delivered += 1
            return self.queue.ack(job, result={"status": body.get("status")})
        if resp.status_code == 422:
            self.failed += 1
            outcome, _ = self.queue.fail(job, Reason.DATA_CORRUPTION,
                                         f"structural reject: {resp.text[:200]}", retryable=False)
            return outcome
        if resp.status_code == 409:
            self.failed += 1
            outcome, _ = self.queue.fail(job, Reason.DATA_CORRUPTION,
                                         f"immutability conflict: {resp.text[:200]}", retryable=False)
            return outcome
        if resp.status_code == 401:
            self.failed += 1
            outcome, _ = self.queue.fail(job, Reason.UNCLASSIFIED,
                                         "ship token rejected (operator misconfig)", retryable=False)
            return outcome
        self.failed += 1
        outcome, _ = self.queue.fail(job, Reason.SERVICE_INTERRUPTION,
                                     f"receiver 5xx: {resp.status_code}", retryable=True)
        return outcome

    def run(self, once: bool = False, wait_timeout_s: float = 0.5,
            idle_exit_after_s: float = 2.0) -> None:
        last_work = time.monotonic()
        reap_at = time.monotonic() + 5.0
        while True:
            self.queue.promote_due()
            if time.monotonic() >= reap_at:
                self.queue.reap_expired()
                reap_at = time.monotonic() + 5.0
            job = self.queue.claim(self.worker_id, wait_timeout_s=wait_timeout_s)
            if job is None:
                if once and time.monotonic() - last_work > idle_exit_after_s:
                    return
                continue
            self.handle_one(job)
            last_work = time.monotonic()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker-id", default=None)
    ap.add_argument("--once", action="store_true",
                    help="drain until idle for a moment, then exit")
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()
    queue = TransmissionQueue(
        name=SYNC_QUEUE_NAME,
        redis_url=os.environ.get("SENTINEL_REDIS_URL", "redis://localhost:6379/0"))
    timeout = float(os.environ.get("TWIN_SYNC_TIMEOUT", str(args.timeout)))
    w = TwinSyncWorker(queue, worker_id=args.worker_id, request_timeout_s=timeout)
    print(f"[twin-sync] {w.worker_id} starting queue={SYNC_QUEUE_NAME}", flush=True)
    try:
        w.run(once=args.once)
    finally:
        print(f"[twin-sync] {w.worker_id} delivered={w.delivered} failed={w.failed}", flush=True)


if __name__ == "__main__":
    main()
