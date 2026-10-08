"""
Parse failures are recorded in every report, not only in the sweep.

A file that does not parse is absent from the call graph. Before this change,
a query for a symbol defined in such a file printed NOT_FOUND, which reads as
"this code does not exist". Each failure is now listed with the SHA-256 of the
exact bytes that failed, so the record names a specific file version.
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

BROKEN_SRC = b"def broken(:\n    pass\n"
GOOD_SRC = "def hello():\n    return 1\n"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "good.py").write_text(GOOD_SRC, encoding="utf-8")
    (root / "broken.py").write_bytes(BROKEN_SRC)
    entries = tmp_path / "entries.txt"
    entries.write_text("good.py\n", encoding="utf-8")
    return str(root), str(entries)


def _run(args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, check=False)


def test_model_records_sha256_of_unparseable_file(tree):
    from model import Graph

    root, _ = tree
    g = Graph(root)
    g.build()
    assert [rel for rel, _ in g.parse_errors] == ["broken.py"]
    digest = hashlib.sha256(BROKEN_SRC).hexdigest()
    assert digest in g.parse_errors[0][1]


def test_query_for_symbol_in_broken_file_does_not_say_not_found_silently(tree):
    root, entries = tree
    proc = _run(["query", "--target", "broken", "--root", root, "--entries-file", entries])
    out = proc.stdout
    assert "broken.py" in out
    assert "could not be parsed" in out
    assert hashlib.sha256(BROKEN_SRC).hexdigest() in out


def test_query_on_a_good_target_still_lists_the_unparsed_file(tree):
    root, entries = tree
    proc = _run(["query", "--target", "hello", "--root", root, "--entries-file", entries])
    assert proc.returncode == 0
    assert "could not be parsed" in proc.stdout
    assert "broken.py" in proc.stdout


def test_sweep_records_the_hash_too(tree):
    root, _ = tree
    proc = _run(["sweep", "--root", root])
    assert "broken.py" in proc.stdout
    assert hashlib.sha256(BROKEN_SRC).hexdigest() in proc.stdout
