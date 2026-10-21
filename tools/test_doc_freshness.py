"""
Tests for tools/doc_freshness.py. Each test builds a throwaway git repo with a
fake COMPLIANCE.md and runs the tool as a subprocess, the way CI does.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(_HERE, "doc_freshness.py")
DOC = "sentinel_os/COMPLIANCE.md"
DAY = 86400
T0 = 1_790_000_000  # a fixed epoch second, so no test depends on today's date


def _git(repo, *args, when=None):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    if when is not None:
        env["GIT_AUTHOR_DATE"] = f"{when} +0000"
        env["GIT_COMMITTER_DATE"] = f"{when} +0000"
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout.strip()


def _commit(repo, files, message, when):
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message, when=when)
    return _git(repo, "rev-parse", "--short", "HEAD")


def _doc(stamp, extra=""):
    return (
        "# Manifest\n\n"
        f"**Last Updated:** Day one. Re-checked against `main` @ `{stamp}`.\n\n"
        "Wiring: `app.py` starts `worker.py`. Removed long ago: `gone.py`.\n"
        f"{extra}"
    )


def _run(repo, *extra):
    proc = subprocess.run(
        [sys.executable, TOOL, "--repo", str(repo), *extra],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(
        tmp_path,
        {"sentinel_os/app.py": "a = 1\n", "sentinel_os/worker.py": "w = 1\n",
         "sentinel_os/test_app.py": "t = 1\n"},
        "base", T0,
    )
    _commit(tmp_path, {DOC: _doc(base)}, "doc", T0 + DAY)
    return tmp_path


def test_fresh_document_passes(repo):
    code, out = _run(repo)
    assert code == 0, out
    assert out.strip() == "FRESH"


def test_claimed_file_changed_after_doc_is_stale(repo):
    _commit(repo, {"sentinel_os/app.py": "a = 2\n"}, "code", T0 + 2 * DAY)
    code, out = _run(repo)
    assert code == 1
    assert out.startswith("STALE")
    assert "sentinel_os/app.py changed after" in out


def test_code_and_doc_in_the_same_commit_counts_as_refresh(repo):
    head = _git(repo, "rev-parse", "--short", "HEAD")
    _commit(
        repo,
        {"sentinel_os/app.py": "a = 2\n", DOC: _doc(head, "More text.\n")},
        "code and doc together", T0 + 2 * DAY,
    )
    code, out = _run(repo)
    assert code == 0, out


def test_doc_touched_after_code_counts_as_refresh(repo):
    _commit(repo, {"sentinel_os/app.py": "a = 2\n"}, "code", T0 + 2 * DAY)
    head = _git(repo, "rev-parse", "--short", "HEAD")
    _commit(repo, {DOC: _doc(head)}, "doc refresh", T0 + 3 * DAY)
    code, out = _run(repo)
    assert code == 0, out


def test_unclaimed_and_test_files_do_not_trigger(repo):
    _commit(
        repo,
        {"sentinel_os/test_app.py": "t = 2\n", "sentinel_os/other.py": "o = 1\n"},
        "unclaimed", T0 + 2 * DAY,
    )
    code, out = _run(repo)
    assert code == 0, out


def test_names_that_no_longer_resolve_are_ignored(repo):
    # gone.py is named in the document but is not a tracked file.
    code, out = _run(repo)
    assert code == 0, out


def test_missing_stamp_fails(repo):
    _commit(repo, {DOC: "# Manifest\n\n**Last Updated:** no stamp here.\n"},
            "no stamp", T0 + 2 * DAY)
    code, out = _run(repo)
    assert code == 1
    assert "no verification stamp" in out


def test_unknown_stamp_fails(repo):
    _commit(repo, {DOC: _doc("deadbeef")}, "bad stamp", T0 + 2 * DAY)
    code, out = _run(repo)
    assert code == 1
    assert "not a commit in this history" in out


def test_old_stamp_fails_on_age(repo):
    _commit(repo, {"sentinel_os/other.py": "o = 1\n"}, "much later", T0 + 60 * DAY)
    code, out = _run(repo)
    assert code == 1
    assert "60 days older than HEAD" in out
    code, out = _run(repo, "--max-age-days", "90")
    assert code == 0, out


def test_missing_document_fails(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, {"sentinel_os/app.py": "a = 1\n"}, "base", T0)
    code, out = _run(tmp_path)
    assert code == 1
    assert "does not exist" in out
