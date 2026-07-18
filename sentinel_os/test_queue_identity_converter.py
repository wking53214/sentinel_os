"""test_queue_identity_converter.py -- live regression lock for F-J:
the ingress (api_server_v2.py) and worker (sentinel_worker.py) used to
have two INDEPENDENT queue-identity settings that had to be manually
kept in sync (TRANSMISSION_NAMESPACE vs --queue-name/SENTINEL_QUEUE_NAME).
With stock defaults on both sides they silently pointed at different
Redis key prefixes -- a submitted job sat at 'pending' forever, the
worker polled an empty keyspace, and nothing anywhere raised an error.

This suite runs both processes together for real (not the Drainer
test_api_server_v2.py otherwise uses) and proves:
  1. Stock defaults, nothing configured beyond the Redis URL, resolve
     to the SAME prefix on both sides and a submitted job actually
     completes -- this is the converter fix itself, proven live.
  2. The shared SENTINEL_QUEUE_ID override still keeps both sides
     aligned when set to a non-default value.
  3. TRANSMISSION_NAMESPACE remains a working escape hatch for direct
     ingress-side control.

Run:  python3 -m pytest test_queue_identity_converter.py -v -s
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
REDIS_URL = "redis://localhost:6379/0"
INGRESS_PORT = 8220


def wait_for(fn, timeout=15.0, interval=0.1, desc="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            out = fn()
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {desc} (last error: {last})")


def sid() -> str:
    return f"QIC{uuid.uuid4().hex}"


def _flush(prefix: str):
    subprocess.run(
        f"redis-cli --scan --pattern '{prefix}:*' | xargs -r redis-cli del",
        shell=True, capture_output=True,
    )


def _spawn(cmd, env_extra, logname):
    env = os.environ.copy()
    env.update(env_extra)
    logf = open(os.path.join(BUILD_DIR, logname), "w")
    return subprocess.Popen(cmd, cwd=BUILD_DIR, env=env, stdout=logf,
                            stderr=subprocess.STDOUT, text=True)


def _run_pair_and_submit(ingress_env: dict, worker_env: dict, port: int,
                          timeout_s: float = 8.0) -> str:
    """Spawn a real ingress + real worker with the given env, submit one
    real governed call, and return its final polled status."""
    ingress_env = {**{"TRANSMISSION_REDIS_URL": REDIS_URL, "PYTHONPATH": BUILD_DIR}, **ingress_env}
    worker_env = {**{"SENTINEL_REDIS_URL": REDIS_URL, "PYTHONPATH": BUILD_DIR}, **worker_env}

    ingress_proc = _spawn(
        [sys.executable, "-m", "uvicorn", "api_server_v2:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        ingress_env, f"qic_ingress_{port}.log",
    )
    worker_proc = _spawn(
        [sys.executable, "sentinel_worker.py", "--worker-id", f"qic-{port}"],
        worker_env, f"qic_worker_{port}.log",
    )
    try:
        wait_for(lambda: httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code == 200,
                 desc=f"ingress /health on :{port}")
        call_sid = sid()
        with httpx.Client(timeout=5.0) as c:
            r = c.post(f"http://127.0.0.1:{port}/submit-call",
                      json={"sid": call_sid, "status": "completed",
                            "from": "+15551234567", "duration": 320, "start_time": 0})
            assert r.status_code == 202, f"submit failed: {r.status_code} {r.text}"

            deadline = time.monotonic() + timeout_s
            last_status = None
            while time.monotonic() < deadline:
                jr = c.get(f"http://127.0.0.1:{port}/job/{call_sid}")
                last_status = jr.json().get("status")
                if last_status == "done":
                    break
                time.sleep(0.2)
        return last_status
    finally:
        ingress_proc.terminate()
        worker_proc.terminate()
        try:
            ingress_proc.wait(timeout=5)
            worker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ingress_proc.kill()
            worker_proc.kill()


def test_stock_defaults_resolve_to_same_prefix_and_job_completes():
    """THE regression this file exists to prevent: nothing configured on
    either side beyond the Redis URL. Before the converter fix, this
    left the ingress on 'tq' and the worker on 'sq:v12' -- a job would
    sit at 'pending' forever. After the fix, both derive 'sq:v12' from
    the same default and the job must actually complete."""
    _flush("tq")
    _flush("sq:v12")
    status = _run_pair_and_submit(ingress_env={}, worker_env={}, port=INGRESS_PORT)
    assert status == "done", (
        f"stock-default ingress/worker pair did not complete a job "
        f"(got status={status!r}) -- the queue-identity converter may "
        f"have regressed"
    )


def test_shared_queue_id_override_keeps_both_sides_aligned():
    """A non-default SENTINEL_QUEUE_ID set on BOTH processes (the
    supported way to run a second, isolated queue) must still resolve
    both sides to the same prefix."""
    _flush("sq:qic_custom")
    status = _run_pair_and_submit(
        ingress_env={"SENTINEL_QUEUE_ID": "qic_custom"},
        worker_env={"SENTINEL_QUEUE_ID": "qic_custom"},
        port=INGRESS_PORT + 1,
    )
    assert status == "done"


def test_transmission_namespace_escape_hatch_still_works():
    """TRANSMISSION_NAMESPACE (ingress-only, direct dialect control)
    must still take precedence over SENTINEL_QUEUE_ID when explicitly
    set, for anyone who wants it -- as long as the worker's queue-name
    is set to match it directly (this is the escape hatch, not the
    converter; alignment here is the operator's explicit choice, not
    automatic, which is the whole point of it being an override)."""
    _flush("manual_ns_match")
    status = _run_pair_and_submit(
        ingress_env={"TRANSMISSION_NAMESPACE": "sq:manual_ns_match"},
        worker_env={"SENTINEL_QUEUE_NAME": "manual_ns_match"},
        port=INGRESS_PORT + 2,
    )
    assert status == "done"


def test_mismatched_explicit_overrides_still_silently_fail():
    """Honest negative case: the escape hatches are still two
    independent knobs by design (that's what makes them useful for
    running deliberately separate queues) -- setting them to genuinely
    different values must NOT complete a job. This documents that the
    converter protects the DEFAULT path, not manual misuse of the
    explicit override knobs."""
    _flush("sq:mismatch_a")
    _flush("sq:mismatch_b")
    status = _run_pair_and_submit(
        ingress_env={"SENTINEL_QUEUE_ID": "mismatch_a"},
        worker_env={"SENTINEL_QUEUE_NAME": "mismatch_b"},
        port=INGRESS_PORT + 3,
        timeout_s=3.0,
    )
    assert status != "done", (
        "expected the job to stay stuck when explicit overrides are "
        "deliberately set to different values -- if this now completes, "
        "something changed about how the escape hatches resolve"
    )
