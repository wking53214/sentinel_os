"""POST /submit-mortgage-decision refuses, at the door, a submission the production worker would dead-letter."""
import os
import shutil
import sys
import subprocess
import tempfile
import time
import uuid

import pytest

PORT = 6396
URL = f"redis://localhost:{PORT}/0"
NAMESPACE = "api-require-events"


@pytest.fixture(scope="module")
def tc():
    import redis as redis_lib
    d = tempfile.mkdtemp(prefix="are-redis-")
    proc = subprocess.Popen(["redis-server", "--port", str(PORT), "--dir", d, "--save", "", "--appendonly", "no",
                             "--logfile", os.path.join(d, "redis.log")])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if redis_lib.Redis(port=PORT, socket_connect_timeout=0.5).ping():
                break
        except Exception:
            time.sleep(0.1)
    prior = {k: os.environ.get(k) for k in ("TRANSMISSION_REDIS_URL", "TRANSMISSION_NAMESPACE")}
    os.environ["TRANSMISSION_REDIS_URL"], os.environ["TRANSMISSION_NAMESPACE"] = URL, NAMESPACE
    from fastapi.testclient import TestClient
    # api_server_v2 reads its Redis settings when imported. Import it fresh for this module and drop it
    # afterwards, so neither this module's settings leak into other suites nor theirs into this one.
    sys.modules.pop("api_server_v2", None)
    import api_server_v2
    with TestClient(api_server_v2.app) as client:
        yield client
    sys.modules.pop("api_server_v2", None)
    for k, v in prior.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(d, ignore_errors=True)


def _event(episode_id):
    return {"event_id": f"{episode_id}:d", "episode_id": episode_id, "domain": "mortgage", "kind": "decision_observed",
            "occurred_at": 1000.0, "observed_at": 1001.0, "source": "loan_system", "provenance": "verified",
            "fields": {"amount": 250000.0}}


def _body(**over):
    eid = "AR" + uuid.uuid4().hex[:10]
    body = {"episode_id": eid, "requested": {"amount": 300000.0}, "events": [_event(eid)],
            "outcome_reasons": ["reduced based on updated appraisal value on file"]}
    body.update(over)
    return body


def _enqueued(episode_id):
    from queue_schema import TransmissionQueue
    return TransmissionQueue(redis_url=URL, namespace=NAMESPACE).get_job(episode_id) is not None


def test_a_submission_with_events_is_accepted(tc):
    body = _body()
    assert tc.post("/submit-mortgage-decision", json=body).status_code == 202
    assert _enqueued(body["episode_id"])


def test_a_submission_without_events_is_422_and_not_enqueued(tc):
    body = _body()
    del body["events"]
    body["actual"] = {"amount": 250000.0}
    r = tc.post("/submit-mortgage-decision", json=body)
    assert r.status_code == 422
    assert not _enqueued(body["episode_id"])


@pytest.mark.parametrize("events", [[], "x", [1], None])
def test_events_must_be_a_non_empty_list_of_objects(tc, events):
    body = _body(events=events)
    assert tc.post("/submit-mortgage-decision", json=body).status_code == 422
    assert not _enqueued(body["episode_id"])


@pytest.mark.parametrize("name", ["actual", "actor_report"])
def test_events_together_with_actual_or_actor_report_is_422(tc, name):
    body = _body(**{name: {"amount": 1.0}})
    r = tc.post("/submit-mortgage-decision", json=body)
    assert r.status_code == 422 and name in r.text
    assert not _enqueued(body["episode_id"])
