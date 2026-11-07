"""Read-route authentication for the twin receiver.

The four GET routes (/meta, /head, /entries, /custody-log) previously took
no credential. They now require the replica's read token, a read-only
credential issued at registration. The ship token, which can write, is not
accepted on the read routes. A replica registered without a read token
refuses all reads (fail closed).
"""
import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="twin receiver read auth needs PostgreSQL (iceberg/iceberg@localhost)")

READ_ROUTES = ("meta", "head", "entries", "custody-log")


def _register(client, rid, **extra):
    body = {"custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
            "customer_sign_pub": "y", "ship_token": "tok", **extra}
    resp = client.post(f"/replica/{rid}/register", json=body)
    assert resp.status_code == 200, resp.text


@pytest.fixture
def readable():
    from twin_receiver import build_app

    client = TestClient(build_app(DSN, site="test"))
    rid = f"read-{uuid.uuid4().hex[:10]}"
    _register(client, rid, read_token="rtok")
    client.replica_id = rid
    client.read = {"Authorization": "Bearer rtok"}
    client.ship = {"Authorization": "Bearer tok"}
    client.bad = {"Authorization": "Bearer wrong-token"}
    return client


@pytest.fixture
def unreadable():
    from twin_receiver import build_app

    client = TestClient(build_app(DSN, site="test"))
    rid = f"noread-{uuid.uuid4().hex[:10]}"
    _register(client, rid)  # no read_token supplied
    client.replica_id = rid
    client.ship = {"Authorization": "Bearer tok"}
    return client


@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_route_requires_read_token(readable, route):
    url = f"/replica/{readable.replica_id}/{route}"
    assert readable.get(url).status_code == 401
    assert readable.get(url, headers=readable.bad).status_code == 401
    assert readable.get(url, headers=readable.ship).status_code == 401
    resp = readable.get(url, headers=readable.read)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("route", READ_ROUTES)
def test_read_route_fails_closed_without_stored_read_token(unreadable, route):
    url = f"/replica/{unreadable.replica_id}/{route}"
    assert unreadable.get(url, headers=unreadable.ship).status_code == 401


def test_meta_does_not_expose_either_token(readable):
    body = readable.get(f"/replica/{readable.replica_id}/meta",
                        headers=readable.read).json()
    assert "ship_token" not in body
    assert "read_token" not in body
