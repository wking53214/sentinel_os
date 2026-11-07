"""Registration is accepted only for operator pre-approved replicas, only with
the pinned customer key, and only with a valid signature from that key."""
import uuid

import psycopg2
import pytest
from fastapi.testclient import TestClient

from twin_custody import generate_signing_keypair, sign_registration

DSN = "host=localhost dbname=iceberg user=iceberg password=iceberg"


def _pg_available() -> bool:
    try:
        psycopg2.connect(DSN + " connect_timeout=2").close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="registration pinning needs PostgreSQL (iceberg/iceberg@localhost)")


def _body(pub, **over):
    return {"custody_model": "A", "recipient_pub": "x", "recipient_fp": "fp",
            "customer_sign_pub": pub, "ship_token": "tok", **over}


def _signed(priv, rid, pub, **over):
    body = _body(pub, **over)
    body["registration_signature"] = sign_registration(rid, body, priv)
    return body


def test_unpinned_replica_is_refused():
    from twin_receiver import build_app
    priv, pub = generate_signing_keypair()
    rid = f"pin-{uuid.uuid4().hex[:8]}"
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={}))
    assert client.post(f"/replica/{rid}/register", json=_signed(priv, rid, pub)).status_code == 403


def test_no_pinned_file_refuses_everything():
    from twin_receiver import build_app
    priv, pub = generate_signing_keypair()
    rid = f"pin-{uuid.uuid4().hex[:8]}"
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys=None))
    assert client.post(f"/replica/{rid}/register", json=_signed(priv, rid, pub)).status_code == 403


def test_key_in_body_must_match_pinned_key():
    from twin_receiver import build_app
    priv, pub = generate_signing_keypair()
    _, other_pub = generate_signing_keypair()
    rid = f"pin-{uuid.uuid4().hex[:8]}"
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    resp = client.post(f"/replica/{rid}/register", json=_signed(priv, rid, other_pub))
    assert resp.status_code == 403


def test_missing_or_wrong_signature_is_refused():
    from twin_receiver import build_app
    priv, pub = generate_signing_keypair()
    rid = f"pin-{uuid.uuid4().hex[:8]}"
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    assert client.post(f"/replica/{rid}/register", json=_body(pub)).status_code == 401
    tampered = _signed(priv, rid, pub)
    tampered["ship_token"] = "swapped"
    assert client.post(f"/replica/{rid}/register", json=tampered).status_code == 401


def test_correctly_signed_registration_is_accepted():
    from twin_receiver import build_app
    priv, pub = generate_signing_keypair()
    rid = f"pin-{uuid.uuid4().hex[:8]}"
    client = TestClient(build_app(DSN, site="test", pinned_customer_keys={rid: pub}))
    resp = client.post(f"/replica/{rid}/register", json=_signed(priv, rid, pub))
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is True
