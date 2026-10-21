"""Named head anchors: a name labels a checkpoint and is covered by its signature.

Unnamed anchors keep the exact shape and signature they had before names
existed; test_verdict_receipts.py pins that shape.
"""
import pytest

import twin_custody as tc
from governance import authorized_by_attestation as att

_KEY = b"named-anchor-test-key-not-a-real-secret"
_OTHER_KEY = b"another-key-the-verifier-never-held"
_HEAD = "a" * 64


def _named(name="close-2026-06"):
    return tc.build_head_anchor(_HEAD, 3, _KEY, sealed_at="2026-06-30T00:00:00+00:00", name=name)


def _unnamed():
    return tc.build_head_anchor(_HEAD, 3, _KEY, sealed_at="2026-06-30T00:00:00+00:00")


def test_a_named_anchor_round_trips_through_its_file_and_verifies(tmp_path):
    path = str(tmp_path / "named.anchor")
    anchor = tc.write_head_anchor(path, _HEAD, 3, _KEY, name="close-2026-06")
    assert tc.read_head_anchor(path)["name"] == "close-2026-06"
    assert tc.verify_head_anchor(anchor, _KEY)[0] == att.STATUS_OK


def test_an_unnamed_anchor_has_no_name_field_and_still_verifies():
    anchor = _unnamed()
    assert "name" not in anchor
    assert tc.verify_head_anchor(anchor, _KEY)[0] == att.STATUS_OK


def test_the_same_chain_under_different_names_gets_different_signatures():
    assert _named("one")["hmac"] != _named("two")["hmac"]


def test_changing_the_name_breaks_the_signature():
    assert tc.verify_head_anchor(dict(_named("close"), name="other"), _KEY)[0] == att.STATUS_INVALID


def test_removing_the_name_breaks_the_signature():
    stripped = {k: v for k, v in _named("close").items() if k != "name"}
    assert tc.verify_head_anchor(stripped, _KEY)[0] == att.STATUS_INVALID


def test_adding_a_name_to_an_unnamed_anchor_breaks_the_signature():
    assert tc.verify_head_anchor(dict(_unnamed(), name="close"), _KEY)[0] == att.STATUS_INVALID


def test_an_empty_name_field_is_refused_even_though_nothing_signed_it():
    assert tc.verify_head_anchor(dict(_unnamed(), name=""), _KEY)[0] == att.STATUS_INVALID


def test_a_named_anchor_under_the_wrong_key_is_unknown_not_valid():
    assert tc.verify_head_anchor(_named("close"), _OTHER_KEY)[0] == att.STATUS_UNKNOWN_KEY


@pytest.mark.parametrize("bad", ["", "   ", "x" * 65, "line\nbreak", "tab\there", 7, ["a"]])
def test_a_name_must_be_short_printable_text(bad):
    with pytest.raises(tc.CustodyError):
        tc.build_head_anchor(_HEAD, 3, _KEY, name=bad)


def test_a_name_of_exactly_sixty_four_characters_is_accepted():
    assert _named("x" * 64)["name"] == "x" * 64
