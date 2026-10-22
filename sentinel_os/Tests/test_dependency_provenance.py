"""Where the git-pinned dependencies actually came from.

requirements.txt installs cns, dit and conservation-kernel straight from their
GitHub repositories. cns and dit are also the names of unrelated packages on
PyPI, and both import as `cns` and `dit`. pip resolves a bare name to the index,
so `pip install -U cns`, or a tool that names the dependency without its
address, silently swaps the pinned package for a stranger's. The stranger's
package then fails at import with an error that points nowhere useful
(`No module named 'cns.DateTimeX'`).

pip records the address and the pinned revision of every direct-URL install in
the package's `direct_url.json`. This test compares that record with the line in
requirements.txt, so a swapped or stale install fails here with the cause
stated, instead of a confusing import error somewhere else.

A deliberate local checkout (an editable install of a sibling repo) is recorded
differently and fails by default. Set SENTINEL_ALLOW_LOCAL_DEPS=1 to skip the
check for those, with the reason shown in the test report.
"""
import json
import os
import re
from importlib import metadata

import pytest

REQUIREMENTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "requirements.txt")
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)\s*@\s*git\+(?P<url>\S+?)@(?P<ref>\S+)\s*$")
FIX = (
    "Fix: in a fresh virtual environment run "
    "`pip install -r sentinel_os/requirements.txt` "
    "(see TROUBLESHOOTING.md, 'Wrong cns or dit installed'). "
    "For a deliberate local checkout, set SENTINEL_ALLOW_LOCAL_DEPS=1."
)


def _normal(url):
    url = url.strip().lower()
    return url[:-4] if url.endswith(".git") else url


def _pins():
    """name -> (repository address, pinned revision), from requirements.txt."""
    pins = {}
    with open(REQUIREMENTS, encoding="utf-8") as handle:
        for line in handle:
            match = _PIN.match(line.strip())
            if match:
                pins[match["name"]] = (_normal(match["url"]), match["ref"])
    return pins


PINS = _pins()


def test_the_name_collision_prone_dependencies_are_pinned_by_address():
    # If this fails, the parser above has stopped seeing the pins and the
    # checks below would pass without checking anything.
    assert {"cns", "dit"} <= set(PINS), sorted(PINS)


@pytest.mark.parametrize("name", sorted(PINS))
def test_the_installed_dependency_came_from_the_pinned_repository(name):
    want_url, want_ref = PINS[name]
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        pytest.fail(f"{name} is not installed. {FIX}")

    raw = dist.read_text("direct_url.json")
    if raw is None:
        pytest.fail(
            f"{name} {dist.version} was installed from a package index, not "
            f"from {want_url}@{want_ref}. A package of the same name on PyPI is "
            f"a different project. {FIX}")

    info = json.loads(raw)
    if "dir_info" in info:
        if os.environ.get("SENTINEL_ALLOW_LOCAL_DEPS") == "1":
            pytest.skip(f"{name} is a local checkout "
                        "(SENTINEL_ALLOW_LOCAL_DEPS=1)")
        pytest.fail(f"{name} {dist.version} is a local checkout "
                    f"({info.get('url')}), not {want_url}@{want_ref}. {FIX}")

    got_url = _normal(info.get("url", ""))
    if got_url != want_url:
        pytest.fail(f"{name} {dist.version} came from {got_url}, but "
                    f"requirements.txt pins {want_url}@{want_ref}. {FIX}")

    got_ref = info.get("vcs_info", {}).get("requested_revision")
    if got_ref != want_ref:
        pytest.fail(f"{name} {dist.version} was installed at revision "
                    f"{got_ref}, but requirements.txt pins {want_ref}. {FIX}")
