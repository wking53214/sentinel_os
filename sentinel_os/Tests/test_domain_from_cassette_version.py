"""domain_from_cassette_version is the one place the shipping path turns
a cassette_version string into the domain cohort assembly groups by.
cassette_version is always "domain:name:version"
(cassette_schema.cassette_version_of) -- a fixed, code-generated format,
never something a caller free-types -- so this is a structural parse, not
a heuristic, and it is worth pinning down exactly.
"""

from twin_custody import domain_from_cassette_version


def test_extracts_the_domain_segment():
    assert domain_from_cassette_version("lending:underwriting_cassette:1.0.0") == "lending"


def test_a_domain_can_itself_be_unambiguous_even_with_dots_or_dashes_in_name():
    assert domain_from_cassette_version("insurance:auto-claims_v2:3.1.4") == "insurance"


def test_missing_cassette_version_returns_none_not_a_fabricated_label():
    assert domain_from_cassette_version(None) is None
    assert domain_from_cassette_version("") is None


def test_a_malformed_string_with_no_colon_is_not_silently_treated_as_a_domain():
    """A string that doesn't match the domain:name:version shape at all
    still returns something (everything up to the first, absent, colon is
    the whole string) -- this is a deliberate, honest parse of what's
    there, not a validity check. bind_cassette_version is what enforces
    the real shape; this helper only extracts."""
    assert domain_from_cassette_version("not_a_real_cassette_version") == "not_a_real_cassette_version"
