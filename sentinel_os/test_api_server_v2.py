"""
test_api_server_v2.py — live verification suite for the v2 ingress.

WHAT THIS IS
  Real chaos evidence, not TestClient theater: a session-scoped fixture
  boots a dedicated redis-server (port 6390) and a real uvicorn process
  running api_server_v2 (port 8102), and every L-series test below talks
  to it over real HTTP with real concurrency (one TCP connection per
  simulated caller). TestClient is used ONLY for the T-series
  routing/validation cases, where in-process is the point.

WHAT DRAINS THE QUEUE HERE
  sentinel_worker.py is not present in this environment, so state
  transitions (processing / scheduled / done / dead) are driven by a
  test Drainer that calls the SAME TransmissionQueue consumer API the
  worker uses: claim() -> heartbeat()/ack()/fail(), plus reap_expired().
  The ingress cannot tell the difference — it only ever reads job
  hashes — but the verification report states this explicitly.

MAP TO THE NON-NEGOTIABLES
  F-A  (no shared breaker; garbage can't gate good callers)
       -> test_L08_garbage_burst_does_not_gate_good_callers
       -> test_L09_dead_jobs_do_not_gate_submission
  F-E  (nothing blocks /health or concurrent requests)
       -> test_L10_stuck_job_does_not_block_the_pipeline
       -> test_L11_frozen_redis_health_stays_alive   (SIGSTOP chaos)
       -> test_L12_submissions_actually_run_concurrently
  202-means-queryable (no window)
       -> test_L07_no_untrackable_202_window
  Never lie about state
       -> test_L05_nonexistent_job_is_a_distinct_404
       -> test_L11 (queue down => 503 "unknown", never 404/fake state)
  Full trajectory observability
       -> test_L01..L04 (pending->processing->done, retry->scheduled,
          non-retryable->dead, exhaustion->dead; polled over HTTP at
          every hop, dead includes reason + error trail)
  Idempotency on sid
       -> test_L06_resubmission_is_idempotent_even_concurrently
  Cheap-reject validation at ingress
       -> T-series (422/413/400 cases) + L08's malformed cohort
  Structural: no harness import, guard seam attaches
       -> test_T10_ingress_never_imports_the_harness
       -> test_L13_auth_seam_attaches_existing_guard

Run:  python3 -m pytest test_api_server_v2.py -v -s
Metrics from the chaos tests are appended to chaos_metrics.json so the
verification report quotes measured numbers, not adjectives.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BUILD_DIR)

from queue_schema import TransmissionQueue  # noqa: E402

REDIS_PORT = 6390
REDIS_URL = f"redis://localhost:{REDIS_PORT}/0"
INGRESS_PORT = 8102
AUTH_INGRESS_PORT = 8103
BASE = f"http://127.0.0.1:{INGRESS_PORT}"
NAMESPACE = f"tqtest_{uuid.uuid4().hex[:8]}"

METRICS: Dict[str, Any] = {"namespace": NAMESPACE}


def sid() -> str:
    return f"CA{uuid.uuid4().hex}"


def wait_for(fn, timeout: float = 15.0, interval: float = 0.1, desc: str = "condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            out = fn()
            if out:
                return out
        except Exception as exc:  # noqa: BLE001 - polling
            last = exc
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {desc} (last error: {last})")


def timed_request(method: str, url: str, fresh_conn: bool = True,
                  timeout: float = 20.0, **kw) -> Tuple[Optional[httpx.Response], float, Optional[str]]:
    """One real HTTP request on its own TCP connection; returns
    (response|None, seconds, error|None)."""
    t0 = time.perf_counter()
    try:
        if fresh_conn:
            with httpx.Client(timeout=timeout) as c:
                r = c.request(method, url, **kw)
        else:
            r = _shared_client.request(method, url, **kw)
        return r, time.perf_counter() - t0, None
    except Exception as exc:  # noqa: BLE001 - chaos tests need the failure mode
        return None, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"


_shared_client = httpx.Client(timeout=20.0)


class Drainer:
    """Test-side consumer speaking the same TransmissionQueue API
    sentinel_worker.py speaks: claim -> ack/fail (+ reap). Small backoff so
    retry tests run in seconds; backoff policy is caller-supplied by design."""

    def __init__(self) -> None:
        self.q = TransmissionQueue(redis_url=REDIS_URL, namespace=NAMESPACE,
                                   backoff_base=0.1, backoff_cap=0.5)

    def claim_specific(self, job_id: str, lease: float = 30.0,
                       worker_id: str = "drainer", timeout: float = 10.0) -> Dict[str, Any]:
        """Claim jobs until job_id comes up; ack any others back? No — in these
        tests each scenario uses its own sids and drains what it claims, so we
        simply keep claiming until the target appears, completing strangers
        (there should be none) defensively."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.q.claim(worker_id=worker_id, lease_seconds=lease)
            if job is None:
                time.sleep(0.05)
                continue
            if job["job_id"] == job_id:
                return job
            # Not ours: finish it so it doesn't linger (shouldn't happen in
            # per-test namespacing, but never leave claims dangling).
            self.q.ack(job["job_id"], job["claim_token"], {"drained_by": "bystander"})
        raise TimeoutError(f"never claimed {job_id}")

    def drain_all(self, n: int, worker_id: str = "drainer") -> int:
        done = 0
        deadline = time.monotonic() + 30
        while done < n and time.monotonic() < deadline:
            job = self.q.claim(worker_id=worker_id)
            if job is None:
                time.sleep(0.02)
                continue
            self.q.ack(job["job_id"], job["claim_token"], {"ok": True})
            done += 1
        return done


# ---------------------------------------------------------------------------
# Session fixtures: dedicated Redis + real uvicorn ingress process.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def redis_proc():
    proc = subprocess.Popen(
        ["redis-server", "--port", str(REDIS_PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def ping():
        with socket.create_connection(("127.0.0.1", REDIS_PORT), timeout=0.5) as s:
            s.sendall(b"PING\r\n")
            return s.recv(16).startswith(b"+PONG")

    wait_for(ping, desc="redis-server PONG")
    yield proc
    try:
        os.kill(proc.pid, signal.SIGCONT)  # in case a chaos test died mid-freeze
    except ProcessLookupError:
        pass
    proc.terminate()
    proc.wait(timeout=5)


def _spawn_ingress(port: int, extra_env: Dict[str, str]) -> subprocess.Popen:
    """Boot a real uvicorn ingress. Server output goes to a FILE, not a pipe:
    the first run of this suite piped stdout and read nothing, and once the
    64KB pipe buffer filled, every logging call in the server blocked the
    thread holding the log lock — a self-inflicted outage that had nothing
    to do with the code under test. A supervisor (systemd/docker) drains
    stdout in production; the test must too."""
    env = os.environ.copy()
    env.update({
        "TRANSMISSION_REDIS_URL": REDIS_URL,
        "TRANSMISSION_NAMESPACE": NAMESPACE,
        "INGRESS_MAX_BODY_BYTES": str(256 * 1024),
        "PYTHONPATH": BUILD_DIR,
        "PYTHONUNBUFFERED": "1",
    })
    env.update(extra_env)
    logpath = os.path.join(BUILD_DIR, f"ingress_{port}.log")
    logf = open(logpath, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api_server_v2:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=BUILD_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_for(lambda: httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code == 200,
                 desc=f"ingress /health on :{port}")
    except TimeoutError:
        logf.flush()
        tail = open(logpath).read()[-2000:]
        raise RuntimeError(f"ingress on :{port} never came up. Log tail:\n{tail}")
    return proc


@pytest.fixture(scope="session")
def ingress(redis_proc):
    TransmissionQueue(redis_url=REDIS_URL, namespace=NAMESPACE).flush_namespace()
    proc = _spawn_ingress(INGRESS_PORT, extra_env={})
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="session")
def drainer(ingress):
    return Drainer()


@pytest.fixture(scope="session", autouse=True)
def dump_metrics():
    yield
    with open(os.path.join(BUILD_DIR, "chaos_metrics.json"), "w") as f:
        json.dump(METRICS, f, indent=2, sort_keys=True)
    print(f"\n[metrics] written to chaos_metrics.json: {json.dumps(METRICS, sort_keys=True)[:400]}...")


# ---------------------------------------------------------------------------
# T-series: routing + cheap-reject validation (TestClient is appropriate here)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tc(redis_proc):
    # Save prior values so this module-scoped fixture doesn't leak
    # TRANSMISSION_REDIS_URL/TRANSMISSION_NAMESPACE into the rest of a
    # full-suite pytest run -- an unrestored os.environ mutation here was
    # observed to leak into test_queue_identity_converter.py's subprocess
    # spawns (which build their env via os.environ.copy()), producing two
    # false failures when both suites run in the same process.
    _prior_redis_url = os.environ.get("TRANSMISSION_REDIS_URL")
    _prior_namespace = os.environ.get("TRANSMISSION_NAMESPACE")
    os.environ["TRANSMISSION_REDIS_URL"] = REDIS_URL
    os.environ["TRANSMISSION_NAMESPACE"] = NAMESPACE
    from fastapi.testclient import TestClient
    import api_server_v2
    with TestClient(api_server_v2.app) as client:
        yield client
    if _prior_redis_url is None:
        os.environ.pop("TRANSMISSION_REDIS_URL", None)
    else:
        os.environ["TRANSMISSION_REDIS_URL"] = _prior_redis_url
    if _prior_namespace is None:
        os.environ.pop("TRANSMISSION_NAMESPACE", None)
    else:
        os.environ["TRANSMISSION_NAMESPACE"] = _prior_namespace


def test_T01_missing_sid_is_422(tc):
    r = tc.post("/submit-call", json={"status": "completed", "duration": 42})
    assert r.status_code == 422


def test_T02_wrong_type_sid_is_422_not_coerced(tc):
    r = tc.post("/submit-call", json={"sid": 12345})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", ["", "   ", " CAabc", "CAabc ", "CA ab", "CA\tab", "CA\x00ab", "CA/../etc"])
def test_T03_unusable_sids_are_422(tc, bad):
    r = tc.post("/submit-call", json={"sid": bad})
    assert r.status_code == 422, f"sid {bad!r} should be rejected"


def test_T04_non_json_body_is_422(tc):
    r = tc.post("/submit-call", content=b"\x00\x01 not json at all",
                headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_T05_oversized_body_is_413_and_not_enqueued(tc):
    big_sid = sid()
    blob = {"sid": big_sid, "junk": "x" * (300 * 1024)}
    r = tc.post("/submit-call", json=blob)
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"
    q = TransmissionQueue(redis_url=REDIS_URL, namespace=NAMESPACE)
    assert q.get_job(big_sid) is None, "413-rejected record must not be enqueued"


def test_T06_valid_minimal_record_is_202_and_immediately_queryable(tc):
    s = sid()
    r = tc.post("/submit-call", json={"sid": s})
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"] == s and body["deduped"] is False and body["status"] == "pending"
    r2 = tc.get(f"/job/{s}")
    assert r2.status_code == 200 and r2.json()["status"] == "pending"


def test_T07_full_record_passes_through_untouched(tc):
    s = sid()
    record = {"sid": s, "status": "completed", "duration": 120,
              "from": "+15551234567", "to": "+billing",
              "nested": {"anything": ["goes", 1, None]}}
    assert tc.post("/submit-call", json=record).status_code == 202
    q = TransmissionQueue(redis_url=REDIS_URL, namespace=NAMESPACE)
    assert q.get_job(s)["payload"] == record, "ingress must not mutate the record"


def test_T08_poll_response_never_echoes_payload(tc):
    s = sid()
    tc.post("/submit-call", json={"sid": s, "from": "+15551234567"})
    body = tc.get(f"/job/{s}").json()
    assert "payload" not in body and "+15551234567" not in json.dumps(body)


def test_T09_health_and_ready_shapes(tc):
    h = tc.get("/health")
    assert h.status_code == 200 and h.json()["status"] == "alive"
    r = tc.get("/ready")
    assert r.status_code == 200 and r.json()["ready"] is True
    s = tc.get("/queue/stats")
    assert s.status_code == 200 and set(s.json()) >= {"pending", "processing", "dead"}


def test_T10_ingress_never_imports_the_harness(tc):
    """The structural F-A/F-E fix: the ingress process must not even LOAD the
    synchronous machinery. Checked two ways: live module table of an imported
    ingress, and the source itself."""
    import ast

    import api_server_v2  # already imported by the fixture

    # 1) The ingress module's OWN namespace must not reference any of the
    #    synchronous machinery. This checks what api_server_v2 itself bound
    #    names to, not global sys.modules -- sys.modules is process-wide
    #    state, so in a full-suite run an unrelated earlier test (e.g.
    #    anything in Tests/ that imports governance.* for its own reasons)
    #    leaves those modules loaded before this test ever runs, which is
    #    not evidence the ingress imported them. Checking api_server_v2's
    #    own module dict isolates "did the ingress's import chain pull this
    #    in" from "is this loaded somewhere in the process."
    forbidden_mods = ("production_harness", "resilient_harness", "sentinel_core")
    bound = []
    for name, val in vars(api_server_v2).items():
        mod_name = getattr(val, "__name__", None)
        if mod_name is None:
            continue
        if mod_name.split(".")[-1] in forbidden_mods or mod_name.startswith("governance"):
            bound.append(f"{name} -> {mod_name}")
    assert not bound, f"ingress namespace references forbidden modules: {bound}"

    # 2) Structural (AST, so the docstring DESCRIBING the old breaker doesn't
    #    trip it): no import of the harness, no construction or naming of a
    #    CircuitBreaker anywhere in actual code.
    tree = ast.parse(open(os.path.join(BUILD_DIR, "api_server_v2.py")).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [a.name for a in node.names]
        elif isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        else:
            continue
        for n in names:
            root = n.split(".")[0]
            assert root not in forbidden_mods, f"forbidden reference in code: {n}"
            assert "governance" != root, f"forbidden reference in code: {n}"
            assert "CircuitBreaker" not in n, "no breaker may exist at ingress"
            assert "IcebergProductionHarness" not in n


# ---------------------------------------------------------------------------
# L-series: live server, real HTTP, real chaos.
# ---------------------------------------------------------------------------
def _submit(record: Dict[str, Any], fresh_conn: bool = False) -> httpx.Response:
    r, _, err = timed_request("POST", f"{BASE}/submit-call", fresh_conn=fresh_conn, json=record)
    assert err is None, f"submit transport failure: {err}"
    return r


def _poll(job_id: str, fresh_conn: bool = False) -> httpx.Response:
    r, _, err = timed_request("GET", f"{BASE}/job/{job_id}", fresh_conn=fresh_conn)
    assert err is None, f"poll transport failure: {err}"
    return r


def test_L01_full_happy_trajectory_pending_processing_done(ingress, drainer):
    s = sid()
    assert _submit({"sid": s, "status": "completed", "duration": 60}).status_code == 202
    assert _poll(s).json()["status"] == "pending"

    job = drainer.claim_specific(s)
    view = _poll(s).json()
    assert view["status"] == "processing"
    assert view["claimed_by"] == "drainer" and view["attempts"] == 1
    assert view["lease_expires_at"] is not None

    assert drainer.q.ack(s, job["claim_token"], {"governance": "ok", "score": 0.91})
    view = _poll(s).json()
    assert view["status"] == "done"
    assert view["result"] == {"governance": "ok", "score": 0.91}
    assert view["completed_at"] is not None
    assert "error_trail" not in view, "clean run must not fabricate an error trail"


def test_L02_retry_trajectory_observed_at_every_hop(ingress, drainer):
    s = sid()
    _submit({"sid": s})
    observed = [_poll(s).json()["status"]]                      # pending

    job = drainer.claim_specific(s)
    observed.append(_poll(s).json()["status"])                  # processing

    assert drainer.q.fail(s, job["claim_token"], "network_latency",
                          "twilio fetch timed out after 5000ms", retryable=True) == "scheduled"
    view = _poll(s).json()
    observed.append(view["status"])                             # scheduled
    assert view["retry_in_s"] >= 0
    assert view["scheduled_for"] is not None
    assert view["error_trail"][0]["reason"] == "network_latency"
    assert "timed out" in view["error_trail"][0]["error"]

    time.sleep(0.25)                                            # past 0.1s backoff
    job2 = drainer.claim_specific(s)
    view = _poll(s).json()
    observed.append(view["status"])                             # processing again
    assert view["attempts"] == 2

    drainer.q.ack(s, job2["claim_token"], {"ok": True})
    view = _poll(s).json()
    observed.append(view["status"])                             # done
    assert view["error_trail"][0]["reason"] == "network_latency", \
        "retry history must survive into the done view"
    assert observed == ["pending", "processing", "scheduled", "processing", "done"]
    METRICS["L02_observed_trajectory"] = observed


def test_L03_nonretryable_failure_dies_with_reason_and_trail(ingress, drainer):
    s = sid()
    _submit({"sid": s, "duration": "not-a-number"})
    job = drainer.claim_specific(s)
    assert drainer.q.fail(s, job["claim_token"], "data_corruption_in_transit",
                          "duration field failed harness parse: 'not-a-number'",
                          retryable=False) == "dead"
    view = _poll(s).json()
    assert view["status"] == "dead"
    assert view["dead_reason"] == "data_corruption_in_transit"
    assert view["died_at"] is not None
    assert view["last_error"].startswith("duration field failed")
    assert len(view["error_trail"]) == 1
    assert view["error_trail"][0]["attempt"] == 1


def test_L04_retry_exhaustion_dies_with_full_trail(ingress, drainer):
    s = sid()
    _submit({"sid": s})
    outcomes = []
    for attempt in range(1, 4):                                  # max_attempts = 3
        job = drainer.claim_specific(s, timeout=15)
        outcomes.append(drainer.q.fail(s, job["claim_token"], "db_connection_loss",
                                       f"ledger unreachable (attempt {attempt})", retryable=True))
        time.sleep(0.35)                                         # let backoff elapse
    assert outcomes == ["scheduled", "scheduled", "dead"]
    view = _poll(s).json()
    assert view["status"] == "dead" and view["dead_reason"] == "db_connection_loss"
    assert [e["attempt"] for e in view["error_trail"]] == [1, 2, 3]
    assert view["attempts"] == 3 == view["max_attempts"]


def test_L05_nonexistent_job_is_a_distinct_404(ingress):
    ghost = f"CA{'0' * 32}"
    r = _poll(ghost)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["error"] == "job_not_found" and detail["job_id"] == ghost
    assert "status" not in detail, "not-found must never look like a status"


def test_L06_resubmission_is_idempotent_even_concurrently(ingress, drainer):
    s = sid()
    record = {"sid": s, "status": "completed"}
    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda _: _submit(record, fresh_conn=True), range(20)))
    assert all(r.status_code == 202 for r in results)
    fresh = [r for r in results if not r.json()["deduped"]]
    assert len(fresh) == 1, f"exactly one enqueue must win; got {len(fresh)}"
    assert all(r.json()["job_id"] == s for r in results)

    job = drainer.claim_specific(s)
    drainer.q.ack(s, job["claim_token"], {"ok": True})
    r = _submit(record)                                          # retry AFTER completion
    body = r.json()
    assert r.status_code == 202 and body["deduped"] is True and body["status"] == "done"
    assert _poll(s).json()["status"] == "done", "resubmission must never reset a finished job"
    METRICS["L06_concurrent_resubmits"] = {"total": 20, "fresh_enqueues": len(fresh)}


def test_L07_no_untrackable_202_window(ingress):
    """Every 202's job_id must be queryable the instant the response exists —
    submit and poll back-to-back on separate connections, under concurrency."""
    def one(_):
        s = sid()
        rs = _submit({"sid": s}, fresh_conn=True)
        rp = _poll(s, fresh_conn=True)
        return rs.status_code, rp.status_code, rp.json().get("status")

    with ThreadPoolExecutor(max_workers=30) as ex:
        out = list(ex.map(one, range(150)))
    not_found = [o for o in out if o[1] == 404]
    assert all(o[0] == 202 for o in out)
    assert not not_found, f"{len(not_found)} jobs were 202'd but momentarily untrackable"
    assert all(o[2] in {"pending", "scheduled", "processing", "done"} for o in out)
    METRICS["L07_submit_then_instant_poll"] = {"pairs": 150, "untrackable": 0}


def test_L08_garbage_burst_does_not_gate_good_callers(ingress):
    """F-A. 300 concurrent callers: 150 valid, 120 malformed, 30 oversized.
    Old shape: 5 failures open the shared breaker -> everyone 5xx for 60s.
    Required here: every valid caller gets a 202 DURING the garbage storm,
    and fresh submissions immediately after are unaffected."""
    valid_sids = [sid() for _ in range(150)]
    garbage = ([{"status": "no sid"}] * 30 + [{"sid": 12345}] * 30 +
               [{"sid": "   "}] * 30 + [{"sid": "CA/../../etc"}] * 30)
    oversized = [{"sid": sid(), "junk": "x" * (300 * 1024)}] * 30

    jobs: List[Tuple[str, Dict[str, Any]]] = (
        [("valid", {"sid": s, "status": "completed", "duration": 30}) for s in valid_sids]
        + [("garbage", g) for g in garbage]
        + [("oversized", o) for o in oversized]
    )
    import random
    random.shuffle(jobs)

    def fire(kind_record):
        kind, record = kind_record
        r, dt, err = timed_request("POST", f"{BASE}/submit-call", json=record)
        return kind, (r.status_code if r else None), dt, err

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=300) as ex:
        out = list(ex.map(fire, jobs))
    wall = time.perf_counter() - t0

    by = {"valid": [], "garbage": [], "oversized": []}
    for kind, code, dt, err in out:
        assert err is None, f"transport failure during burst: {err}"
        by[kind].append((code, dt))

    assert all(c == 202 for c, _ in by["valid"]), \
        f"good callers failed during garbage storm: {[c for c, _ in by['valid'] if c != 202]}"
    assert all(c == 422 for c, _ in by["garbage"])
    assert all(c == 413 for c, _ in by["oversized"])

    # Every 202 is a real, queryable job.
    with ThreadPoolExecutor(max_workers=50) as ex:
        polls = list(ex.map(lambda s: _poll(s, fresh_conn=True).status_code, valid_sids))
    assert polls.count(200) == 150

    # And the storm latched nothing: fresh submissions right after are clean.
    after = [timed_request("POST", f"{BASE}/submit-call", json={"sid": sid()}) for _ in range(20)]
    assert all(r.status_code == 202 for r, _, _ in after)
    lat_after = sorted(dt for _, dt, _ in after)

    lat_valid = sorted(dt for _, dt in by["valid"])
    METRICS["L08_garbage_burst"] = {
        "concurrent_callers": 300, "wall_s": round(wall, 3),
        "valid_202": 150, "garbage_422": len(by["garbage"]), "oversized_413": 30,
        "valid_p50_s": round(median(lat_valid), 4),
        "valid_p99_s": round(lat_valid[int(0.99 * len(lat_valid)) - 1], 4),
        "post_burst_p95_s": round(lat_after[int(0.95 * len(lat_after)) - 1], 4),
    }
    print(f"\n[L08] {METRICS['L08_garbage_burst']}")


def test_L09_dead_jobs_do_not_gate_submission(ingress, drainer):
    """F-A counterfactual: in the old code, 5+ failures opened the shared
    breaker for ALL callers. Here we kill 10 jobs into the DLQ and prove
    submission for everyone else is untouched, immediately."""
    for _ in range(10):
        s = sid()
        _submit({"sid": s})
        job = drainer.claim_specific(s)
        assert drainer.q.fail(s, job["claim_token"], "unclassified",
                              "poisoned record", retryable=False) == "dead"

    lat = []
    for _ in range(20):
        r, dt, err = timed_request("POST", f"{BASE}/submit-call", json={"sid": sid()})
        assert err is None and r.status_code == 202
        lat.append(dt)
    lat.sort()
    p95 = lat[int(0.95 * len(lat)) - 1]
    assert p95 < 0.5, f"submission degraded after DLQ deaths (p95={p95:.3f}s)"
    METRICS["L09_after_10_dead"] = {"submit_p95_s": round(p95, 4), "all_202": True}


def test_L10_stuck_job_does_not_block_the_pipeline(ingress, drainer):
    """F-E at the pipeline level: one job stuck in `processing` (claimed,
    never acked, 60s lease) while 40 other jobs flow to done around it and
    /health + polls stay fast."""
    stuck = sid()
    _submit({"sid": stuck})
    stuck_job = drainer.claim_specific(stuck, lease=60.0, worker_id="stuck-worker")

    others = [sid() for _ in range(40)]
    health_lat: List[float] = []
    health_bad: List[str] = []
    stop = threading.Event()

    def hammer_health():
        with httpx.Client(timeout=5.0) as c:   # own persistent connection
            while not stop.is_set():
                t0 = time.perf_counter()
                try:
                    r = c.get(f"{BASE}/health")
                    health_lat.append(time.perf_counter() - t0)
                    if r.status_code != 200:
                        health_bad.append(f"status {r.status_code}")
                except Exception as exc:  # noqa: BLE001
                    health_bad.append(f"{type(exc).__name__}: {exc}")
                time.sleep(0.01)

    ht = threading.Thread(target=hammer_health)
    ht.start()
    try:
        with ThreadPoolExecutor(max_workers=20) as ex:
            codes = list(ex.map(lambda s: _submit({"sid": s}, fresh_conn=True).status_code, others))
        assert codes == [202] * 40
        drained = drainer.drain_all(40, worker_id="flowing-worker")
        assert drained == 40
    finally:
        stop.set()
        ht.join()

    with ThreadPoolExecutor(max_workers=20) as ex:
        states = list(ex.map(lambda s: _poll(s, fresh_conn=True).json()["status"], others))
    assert states.count("done") == 40, f"pipeline stalled around stuck job: {set(states)}"
    assert _poll(stuck).json()["status"] == "processing", "stuck job must still be visibly stuck"

    assert not health_bad, f"/health failed during stuck-job window: {health_bad[:5]}"
    health_lat.sort()
    p99 = health_lat[int(0.99 * len(health_lat)) - 1]
    assert p99 < 0.25, f"/health degraded while a job was stuck (p99={p99:.3f}s)"
    METRICS["L10_stuck_job"] = {
        "others_done": 40, "stuck_status": "processing",
        "health_samples": len(health_lat), "health_p99_s": round(p99, 4),
    }
    print(f"\n[L10] {METRICS['L10_stuck_job']}")

    # cleanup: let the stuck job die honestly
    drainer.q.fail(stuck, stuck_job["claim_token"], "service_interruption",
                   "test worker never recovered", retryable=False)


def test_L11_frozen_redis_health_stays_alive(ingress, redis_proc, drainer):
    """F-E worst case: Redis itself frozen (SIGSTOP — connections accepted by
    the kernel, every command hangs). Required: /health unaffected; every
    queue-touching request returns an HONEST 503 within its socket-timeout
    budget (never a hang, never a 404, never a fake state); full recovery
    after SIGCONT."""
    r, base_health, _ = timed_request("GET", f"{BASE}/health")
    assert r.status_code == 200

    # Dedicated /health probe: its own thread, its own PERSISTENT connection,
    # sampling continuously through the whole freeze. This isolates "is the
    # server's event loop responsive" from client-side thread contention in
    # the load generator (which contaminated run 1's numbers).
    probe_lat: List[float] = []
    probe_bad: List[str] = []
    probe_stop = threading.Event()

    def probe():
        with httpx.Client(timeout=5.0) as c:
            while not probe_stop.is_set():
                t0 = time.perf_counter()
                try:
                    r = c.get(f"{BASE}/health")
                    probe_lat.append(time.perf_counter() - t0)
                    if r.status_code != 200:
                        probe_bad.append(f"status {r.status_code}")
                except Exception as exc:  # noqa: BLE001
                    probe_bad.append(f"{type(exc).__name__}: {exc}")
                time.sleep(0.025)

    pt = threading.Thread(target=probe, daemon=True)
    pt.start()
    time.sleep(0.3)  # baseline samples with Redis healthy

    os.kill(redis_proc.pid, signal.SIGSTOP)
    try:
        # Measure the single-call degraded budget first (socket_timeout=2s
        # per attempt; the client library's retry policy multiplies it).
        r, single, err = timed_request("GET", f"{BASE}/ready")
        assert err is None and r.status_code == 503
        budget = max(4.0, 3 * single + 1.0)

        def submit(_):
            return timed_request("POST", f"{BASE}/submit-call", json={"sid": sid()})

        def poll(_):
            return timed_request("GET", f"{BASE}/job/CA{'f' * 32}")

        with ThreadPoolExecutor(max_workers=30) as ex:
            fs = [ex.submit(submit, i) for i in range(15)]
            fp = [ex.submit(poll, i) for i in range(15)]
            submit_out = [f.result() for f in fs]
            poll_out = [f.result() for f in fp]

        probe_stop.set()
        pt.join()
        assert not probe_bad, f"/health failed during freeze: {probe_bad[:5]}"
        h_lat = sorted(probe_lat)
        h_max = h_lat[-1]
        assert h_max < 0.25, f"/health blocked by frozen Redis (max={h_max:.3f}s)"

        for group, outs in (("submit", submit_out), ("poll", poll_out)):
            for r, dt, err in outs:
                assert err is None, f"{group} hung past client timeout: {err}"
                assert r.status_code == 503, f"{group} lied under outage: {r.status_code}"
                assert r.json()["detail"]["error"] == "queue_unavailable"
                assert "Retry-After" in r.headers
                assert dt < budget, f"{group} exceeded degraded budget ({dt:.2f}s > {budget:.2f}s)"

        q_lat = sorted([dt for _, dt, _ in submit_out] + [dt for _, dt, _ in poll_out])
        METRICS["L11_frozen_redis"] = {
            "health_probe_samples": len(probe_lat), "health_probe_max_s": round(h_max, 4),
            "health_probe_p50_s": round(median(h_lat), 4),
            "health_baseline_single_s": round(base_health, 4),
            "degraded_single_call_s": round(single, 3),
            "queue_503_p50_s": round(median(q_lat), 3), "queue_503_max_s": round(q_lat[-1], 3),
            "budget_s": round(budget, 2), "all_503_honest": True,
        }
        print(f"\n[L11] {METRICS['L11_frozen_redis']}")
    finally:
        probe_stop.set()
        os.kill(redis_proc.pid, signal.SIGCONT)

    def recovered():
        r, _, err = timed_request("POST", f"{BASE}/submit-call", json={"sid": sid()})
        return err is None and r.status_code == 202

    wait_for(recovered, timeout=10, desc="post-thaw 202")
    r, _, _ = timed_request("GET", f"{BASE}/ready")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_L12_submissions_actually_run_concurrently(ingress):
    """F-E 'not by inspection': if handlers serialized on the event loop,
    wall time for N submissions ≈ sum of individual latencies. Prove real
    overlap: wall must be a small fraction of the serial sum."""
    n, workers = 200, 50

    def one(_):
        return timed_request("POST", f"{BASE}/submit-call", json={"sid": sid()})

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(one, range(n)))
    wall = time.perf_counter() - t0

    from collections import Counter
    outcomes = Counter(
        (err.split(":")[0] if err else str(r.status_code)) for r, _, err in out
    )
    assert outcomes == Counter({"202": n}), f"non-202 outcomes under load: {dict(outcomes)}"
    serial_sum = sum(dt for _, dt, _ in out)
    overlap = serial_sum / wall
    assert overlap > 4, f"requests are serializing (overlap factor {overlap:.1f}x)"
    lat = sorted(dt for _, dt, _ in out)
    METRICS["L12_concurrency"] = {
        "n": n, "client_threads": workers, "wall_s": round(wall, 3),
        "serial_sum_s": round(serial_sum, 2), "overlap_factor": round(overlap, 1),
        "p50_s": round(median(lat), 4), "p99_s": round(lat[int(0.99 * n) - 1], 4),
        "throughput_rps": round(n / wall, 1),
    }
    print(f"\n[L12] {METRICS['L12_concurrency']}")


def test_L14_throughput_floor_with_noncontending_generator(ingress):
    """L08/L12 measured ~23 rps — but their generator is 50–300 Python
    threads each building a fresh httpx.Client, which serializes on the
    test process's GIL. Little's law can't tell you WHERE the queueing
    happened, so this test removes the generator as a variable: one
    thread, one asyncio loop, 100 persistent keepalive connections. If
    the server were the bottleneck, this would measure the same ~23 rps;
    if the generator was, this measures the server's real floor."""
    import asyncio
    from collections import Counter

    async def run():
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=100)
        async with httpx.AsyncClient(timeout=20.0, limits=limits) as c:
            sem = asyncio.Semaphore(100)

            async def one(_):
                async with sem:
                    t0 = time.perf_counter()
                    r = await c.post(f"{BASE}/submit-call", json={"sid": sid()})
                    return r.status_code, time.perf_counter() - t0

            t0 = time.perf_counter()
            out = await asyncio.gather(*[one(i) for i in range(500)])
            return out, time.perf_counter() - t0

    out, wall = asyncio.run(run())
    codes = Counter(c for c, _ in out)
    assert codes == Counter({202: 500}), f"non-202 under load: {dict(codes)}"
    lat = sorted(dt for _, dt in out)
    rps = 500 / wall
    # Measured on this container: /health alone ~80 rps, full submit path
    # ~73-85 rps — the ceiling is the box's CPU on HTTP handling itself;
    # validate+enqueue+log adds ~1.2ms over the routing baseline. The floor
    # here is a canary against pathological serialization (a regression to
    # the old blocked-event-loop shape collapses this to single digits),
    # not a capacity claim; L12's overlap factor is the concurrency proof.
    assert rps > 50, f"server throughput floor breached: {rps:.0f} rps"
    METRICS["L14_throughput_asyncio"] = {
        "n": 500, "concurrency": 100, "wall_s": round(wall, 3),
        "rps": round(rps, 1), "p50_s": round(median(lat), 4),
        "p99_s": round(lat[int(0.99 * 500) - 1], 4),
    }
    print(f"\n[L14] {METRICS['L14_throughput_asyncio']}")


def test_L13_auth_seam_attaches_existing_guard(redis_proc):
    """The guard seam works with today's production auth, unmodified:
    a second ingress with ICEBERG_API_KEYS set must 401/403 unkeyed and
    wrong-keyed submissions, accept keyed ones, and keep /health open."""
    inner = os.path.join(os.path.dirname(BUILD_DIR), "sentinel_os", "sentinel_os")
    proc = _spawn_ingress(AUTH_INGRESS_PORT, extra_env={
        "ICEBERG_API_KEYS": "testkey-abc123:pytest",
        "PYTHONPATH": f"{BUILD_DIR}:{inner}",
    })
    base = f"http://127.0.0.1:{AUTH_INGRESS_PORT}"
    try:
        assert httpx.get(f"{base}/health", timeout=5).status_code == 200
        assert httpx.post(f"{base}/submit-call", json={"sid": sid()}, timeout=5).status_code == 401
        assert httpx.post(f"{base}/submit-call", json={"sid": sid()},
                          headers={"x-api-key": "wrong"}, timeout=5).status_code == 403
        ok = httpx.post(f"{base}/submit-call", json={"sid": sid()},
                        headers={"x-api-key": "testkey-abc123"}, timeout=5)
        assert ok.status_code == 202
        assert httpx.get(f"{base}/job/{ok.json()['job_id']}", timeout=5).status_code == 401
        assert httpx.get(f"{base}/job/{ok.json()['job_id']}",
                         headers={"x-api-key": "testkey-abc123"}, timeout=5).status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)
