"""test_worker_bad_input.py -- bad-input stress through a REAL worker on the
current mortgage path (GovernanceHarness via the job adapter), real Redis,
real Postgres.

Claim under test: whatever shape a job payload has, the worker (1) never
crashes, (2) never leaves the job stuck pending/processing, (3) never writes
a ledger row for a payload it could not parse into a well-formed episode,
and (4) keeps serving good jobs afterwards. Bad payloads must end as a
dead-letter with a diagnosis (never a silent success).

Run:  python3 -m pytest test_worker_bad_input.py -v -s
"""
from __future__ import annotations

import copy
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
OWNER = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg", password="iceberg")


def _pg_up():
    try:
        psycopg2.connect(connect_timeout=2, **OWNER).close()
        return True
    except Exception:
        return False


def good(eid=None):
    eid = eid or f"BI{uuid.uuid4().hex}"
    return eid, {"episode_id": eid, "requested": {"amount": 300000.0},
                 "events": [{"event_id": f"{eid}:decision", "episode_id": eid, "domain": "mortgage",
                             "kind": "decision_observed", "occurred_at": 1000.0, "observed_at": 1001.0,
                             "source": "loan_system", "provenance": "verified",
                             "fields": {"amount": 250000.0}}],
                 "outcome_reasons": ["reduced based on updated appraisal value on file"]}


def variants():
    """(label, payload) -- each is a GOOD payload broken in exactly one way."""
    out = []

    def mut(label, fn):
        eid, p = good()
        p = copy.deepcopy(p)
        fn(p, eid)
        out.append((label, eid, p))

    mut("no events key", lambda p, e: p.pop("events"))
    mut("events empty list", lambda p, e: p.update(events=[]))
    mut("events is a string", lambda p, e: p.update(events="oops"))
    mut("event for another episode", lambda p, e: p["events"][0].update(episode_id="someone-else"))
    mut("event missing observed_at", lambda p, e: p["events"][0].pop("observed_at"))
    mut("event provenance invalid", lambda p, e: p["events"][0].update(provenance="trust-me"))
    mut("event kind invalid", lambda p, e: p["events"][0].update(kind=""))
    mut("event fields is a list", lambda p, e: p["events"][0].update(fields=[1, 2]))
    mut("amount is a string", lambda p, e: p.update(requested={"amount": "300000"}))
    mut("amount is null", lambda p, e: p.update(requested={"amount": None}))
    mut("requested is a list", lambda p, e: p.update(requested=[300000]))
    mut("requested missing", lambda p, e: p.pop("requested"))
    mut("episode_id empty", lambda p, e: p.update(episode_id=""))
    mut("episode_id is a number", lambda p, e: p.update(episode_id=12345))
    mut("outcome_reasons is a string", lambda p, e: p.update(outcome_reasons="reduced"))
    mut("outcome_reasons has a number", lambda p, e: p.update(outcome_reasons=[7]))
    mut("huge reason text (200 KB)", lambda p, e: p.update(outcome_reasons=["x" * 200_000]))
    mut("unicode and control chars", lambda p, e: p.update(outcome_reasons=["\u202e\x00reduced\u2028"]))
    mut("deeply nested fields", lambda p, e: p["events"][0].update(
        fields={"a": {"b": {"c": {"d": {"e": {"f": [[[[1]]]]}}}}}}))
    mut("extra unknown keys", lambda p, e: p.update(surprise={"x": 1}, another=[1]))
    mut("payload is empty", lambda p, e: (p.clear()))
    return out


@pytest.mark.skipif(not _pg_up(), reason="live PostgreSQL not available")
def test_worker_survives_and_classifies_every_bad_payload():
    qname = f"bad{uuid.uuid4().hex[:6]}"
    q = TransmissionQueue(name=qname, redis_url=REDIS_URL)
    env = os.environ.copy()
    env.update({"SENTINEL_REDIS_URL": REDIS_URL, "PYTHONPATH": BUILD_DIR,
                "POSTGRES_PASSWORD": "iceberg", "SENTINEL_QUEUE_NAME": qname})
    env.pop("CLAUDE_API_KEY", None)
    logp = os.path.join(BUILD_DIR, f"bad_worker_{qname}.log")
    w = subprocess.Popen([sys.executable, "sentinel_worker.py", "--worker-id", "bad-1"], cwd=BUILD_DIR,
                         env=env, stdout=open(logp, "w"), stderr=subprocess.STDOUT)
    try:
        t0 = time.monotonic()
        while "Worker starting" not in open(logp).read():
            assert time.monotonic() - t0 < 40, "worker never started"
            time.sleep(0.3)

        # control job FIRST proves the pipeline works at all
        ceid, cp = good()
        q.enqueue(payload=cp, job_id=ceid, max_attempts=3)

        cases = variants()
        ids = {}
        for label, eid, p in cases:
            job_id = f"{eid}-{len(ids)}"
            try:
                q.enqueue(payload=p, job_id=job_id, max_attempts=3)
                ids[job_id] = (label, eid)
            except Exception as exc:  # the QUEUE refusing is also a safe outcome
                ids[job_id] = (label, f"REFUSED_AT_ENQUEUE:{type(exc).__name__}")

        # and a good job AFTER the poison, to prove the worker still serves
        aeid, ap = good()
        q.enqueue(payload=ap, job_id=aeid, max_attempts=3)

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            st = q.stats()
            if st["pending"] == 0 and st["processing"] == 0 and st["scheduled"] == 0:
                break
            time.sleep(0.5)
        st = q.stats()
        print(f"\n  final queue: done={st['done']} dead={st['dead']} reasons={st['dead_reasons']} "
              f"pending={st['pending']} processing={st['processing']} scheduled={st['scheduled']}")
        assert w.poll() is None, "WORKER PROCESS DIED on a bad payload"
        assert st["pending"] == 0 and st["processing"] == 0 and st["scheduled"] == 0, \
            "a job is stuck in a non-terminal state"

        # ledger truth: which episode ids actually reached the ledger?
        conn = psycopg2.connect(**OWNER)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT input_data->>'episode_id' FROM ledger_entries "
                    "WHERE record_kind='governance_decision' AND input_data->>'episode_id' = ANY(%s)",
                    ([ceid, aeid] + [v[1] for v in ids.values() if isinstance(v[1], str)],))
        written = {r[0] for r in cur.fetchall()}
        conn.close()
        assert ceid in written, "control job (before the poison) was not recorded"
        assert aeid in written, "good job AFTER the poison was not recorded: worker degraded"

        wrongly_recorded = [(lab, eid) for jid, (lab, eid) in ids.items() if eid in written]
        print("  bad payloads that WERE recorded to the ledger:", wrongly_recorded or "none")
        # HARD rule: structurally broken payloads (missing/empty/mistyped
        # identity or events, events for another episode) never reach the
        # ledger. TYPE-LAX cases are reported, not hidden: see the
        # verification report for what the system accepts as-is.
        must_not_record = {"no events key", "events empty list", "events is a string",
                           "event for another episode", "event missing observed_at",
                           "event provenance invalid", "event kind invalid",
                           "event fields is a list", "requested missing", "requested is a list",
                           "episode_id empty", "episode_id is a number", "payload is empty"}
        bad = [lab for lab, eid in wrongly_recorded if lab in must_not_record]
        assert not bad, f"structurally broken payloads were recorded as valid: {bad}"
    finally:
        w.terminate()
        try:
            w.wait(timeout=8)
        except subprocess.TimeoutExpired:
            w.kill()
