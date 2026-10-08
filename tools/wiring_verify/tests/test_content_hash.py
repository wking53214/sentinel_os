"""
Every report ends with the SHA-256 of everything above it. Two runs over the
same source tree produce the same digest, and a changed tree produces a
different one. A reviewer can compare two reports by their last line alone.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.dirname(_HERE)
CLI = os.path.join(_TOOL_DIR, "cli.py")
MARKER = "Report SHA-256"

SRC = """\
def entry():
    helper()


def helper():
    return 1
"""


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "mod.py").write_text(SRC, encoding="utf-8")
    entries = tmp_path / "entries.txt"
    entries.write_text("mod.py:entry\n", encoding="utf-8")
    return str(root), str(entries)


def _run(args, seed="0"):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    proc = subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, env=env, check=False)
    return proc.stdout


def _split_footer(out: str):
    """Returns (body, digest). The body is everything before the separator line."""
    idx = out.rindex(MARKER)
    body = out[: out.rindex("\n---\n", 0, idx)]
    footer_digest = out[idx:].strip().rsplit(":", 1)[-1].strip()
    return body, footer_digest


def test_query_footer_matches_body(tree):
    root, entries = tree
    out = _run(["query", "--target", "helper", "--root", root, "--entries-file", entries])
    body, digest = _split_footer(out)
    assert digest == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_sweep_footer_matches_body(tree):
    root, _ = tree
    out = _run(["sweep", "--root", root, "--verbose"])
    body, digest = _split_footer(out)
    assert digest == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_sweep_output_and_digest_repeat_across_hash_seeds(tree):
    root, _ = tree
    outs = {_run(["sweep", "--root", root, "--verbose"], seed=s) for s in ("1", "2", "3", "4")}
    assert len(outs) == 1


def test_digest_changes_when_the_tree_changes(tree):
    root, _ = tree
    before = _split_footer(_run(["sweep", "--root", root]))[1]
    with open(os.path.join(root, "mod.py"), "a", encoding="utf-8") as f:
        f.write("\n\ndef extra():\n    return 2\n")
    after = _split_footer(_run(["sweep", "--root", root]))[1]
    assert before != after
