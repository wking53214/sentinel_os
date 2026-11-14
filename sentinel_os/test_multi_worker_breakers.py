"""test_multi_worker_breakers.py -- live proof, with TWO real worker
processes sharing one queue and one Postgres, that:

  * each worker's postgres_ledger breaker trips on its own, from its own
    real failures (no state is shared between worker processes);
  * while open, nothing is lost: jobs keep retrying, none dead-letter;
  * when the database becomes writable again, both workers' breakers
    recover (HALF_OPEN then CLOSED) and every waiting job completes.

The outage is real: the workers' database role is genuinely switched to
read-only and its live sessions are terminated. Claude is not involved (no
key is configured, so the governor fails closed the ordinary way), which
isolates the ledger path.

Run:  python3 -m pytest test_multi_worker_breakers.py -v -s
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import psycopg2
import pytest

from queue_schema import TransmissionQueue

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
REDIS_URL = "redis://localhost:6379/0"
QNAME = f"mwb{uuid.uuid4().hex[:6]}"
ROLE, PW = "brk_mw_test", "brk_mw_pw"
OWNER = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")


def _pg_up():
    try:
        psycopg2.connect(connect_timeout=2, **OWNER).close()
        return True
    except Exception:
        return False


def wait_for(fn, timeout, interval=0.3, desc="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(interval)
    raise TimeoutError(desc)


def _payload():
    eid = f"MW{uuid.uuid4().hex}"
    return eid, {"episode_id": eid, "requested": {"amount": 300000.0},
                 "events": [{"event_id": f"{eid}:decision", "episode_id": eid, "domain": "mortgage",
                             "kind": "decision_observed", "occurred_at": 1000.0, "observed_at": 1001.0,
                             "source": "loan_system", "provenance": "verified",
                             "fields": {"amount": 250000.0}}],
                 "outcome_reasons": ["reduced based on updated appraisal value on file"]}


@pytest.mark.skipif(not _pg_up(), reason="live PostgreSQL not available")
def test_two_workers_trip_independently_lose_nothing_and_recover():
    own = psycopg2.connect(**OWNER)
    own.autocommit = True
    cur = own.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PW}'")
    cur.execute(f"ALTER ROLE {ROLE} RESET default_transaction_read_only")
    cur.execute(f"GRANT ledger_reader TO {ROLE}")

    q = TransmissionQueue(name=QNAME, redis_url=REDIS_URL)
    env = os.environ.copy()
    env.update({"SENTINEL_REDIS_URL": REDIS_URL, "PYTHONPATH": BUILD_DIR,
                "POSTGRES_PASSWORD": "iceberg", "SENTINEL_QUEUE_NAME": QNAME,
                "ICEBERG_LEDGER_RUNTIME_USER": ROLE, "ICEBERG_LEDGER_RUNTIME_PASSWORD": PW,
                "LEDGER_STATEMENT_TIMEOUT_S": "5"})
    env.pop("CLAUDE_API_KEY", None)
    logs, procs = {}, []
    for wid in ("mwA", "mwB"):
        logs[wid] = os.path.join(BUILD_DIR, f"mwb_{wid}.log")
        procs.append(subprocess.Popen([sys.executable, "sentinel_worker.py", "--worker-id", wid],
                                      cwd=BUILD_DIR, env=env, stdout=open(logs[wid], "w"),
                                      stderr=subprocess.STDOUT))

    def log_has(wid, *needles):
        txt = open(logs[wid]).read()
        return all(n in txt for n in needles)

    def done(ids):
        return all(q.get_job(i) and q.get_job(i).get("status") == "done" for i in ids) if hasattr(q, "get_job") else None

    try:
        wait_for(lambda: all(log_has(w, "Worker starting") for w in logs), 40, desc="workers up")

        # 1. healthy
        healthy = [_payload() for _ in range(4)]
        for eid, p in healthy:
            q.enqueue(payload=p, job_id=eid, max_attempts=50)
        wait_for(lambda: q.stats()["done"] >= 4, 40, desc="healthy jobs done")

        # 2. real outage
        cur.execute(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on")
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s", (ROLE,))
        stuck = [_payload() for _ in range(12)]
        for eid, p in stuck:
            q.enqueue(payload=p, job_id=eid, max_attempts=50)
        wait_for(lambda: all(log_has(w, '"breaker": "postgres_ledger"', '"action": "opened"') for w in logs),
                 60, desc="BOTH workers' own ledger breakers open")
        st = q.stats()
        assert st["dead"] == 0, f"nothing may dead-letter during the outage: {st['dead']}"
        assert st["done"] == 4, "no new job can complete while the ledger cannot be written"
        # Claude breaker never moved in either worker
        for w in logs:
            assert not log_has(w, '"breaker": "claude_governor"', '"action": "opened"')

        # 3. recovery
        cur.execute(f"ALTER ROLE {ROLE} RESET default_transaction_read_only")
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s", (ROLE,))
        wait_for(lambda: q.stats()["done"] >= 16, 120, desc="all 12 waiting jobs complete after recovery")
        for w in logs:
            assert log_has(w, "circuit entering HALF_OPEN"), f"{w} never probed"
        assert any(log_has(w, "circuit CLOSED (recovered)") for w in logs)
        assert q.stats()["dead"] == 0
    finally:
        cur.execute(f"ALTER ROLE {ROLE} RESET default_transaction_read_only")
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
        own.close()
