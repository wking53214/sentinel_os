"""Registration signature helper: the customer signs, the receiver verifies.

Covers the shared payload definition used by both sides. The receiver's
enforcement is tested separately.
"""
import twin_custody as tc


def _body(**over):
    body = {"custody_model": "A", "recipient_pub": "rp", "recipient_fp": "fp",
            "customer_sign_pub": "", "max_lag_seconds": 30, "retention_days": 2557,
            "is_primary_evidence": False, "ship_token": "tok"}
    body.update(over)
    return body


def test_signature_verifies_for_the_signed_body():
    priv, pub = tc.generate_signing_keypair()
    body = _body(customer_sign_pub=pub)
    sig = tc.sign_registration("rep-1", body, priv)
    assert tc.verify_registration("rep-1", body, sig, pub)


def test_changing_any_signed_field_breaks_the_signature():
    priv, pub = tc.generate_signing_keypair()
    body = _body(customer_sign_pub=pub)
    sig = tc.sign_registration("rep-1", body, priv)
    assert not tc.verify_registration("rep-1", _body(customer_sign_pub=pub, ship_token="other"), sig, pub)
    assert not tc.verify_registration("rep-2", body, sig, pub)
    assert not tc.verify_registration("rep-1", _body(customer_sign_pub=pub, max_lag_seconds=5), sig, pub)


def test_signature_from_another_key_is_rejected():
    priv, _pub = tc.generate_signing_keypair()
    _, other_pub = tc.generate_signing_keypair()
    body = _body(customer_sign_pub=other_pub)
    sig = tc.sign_registration("rep-1", body, priv)
    assert not tc.verify_registration("rep-1", body, sig, other_pub)


def test_unsigned_fields_do_not_affect_the_payload():
    body = _body(customer_sign_pub="k", unrelated="x")
    assert tc.registration_payload("r", body) == tc.registration_payload("r", _body(customer_sign_pub="k"))
