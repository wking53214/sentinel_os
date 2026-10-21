"""
Repeatable output: the same source tree must give the same report bytes,
no matter what order Python happens to iterate its sets in.

Python randomises string hashing per process (PYTHONHASHSEED), which changes
set iteration order. Any traversal that walks a set without sorting can pick a
different first-found call chain from run to run.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.dirname(_HERE)
CLI = os.path.join(_TOOL_DIR, "cli.py")

# entry -> x -> t and entry -> y -> t. Two equally short paths to t, so the
# reported chain depends on which neighbour the traversal visits first.
DIAMOND_SRC = """\
def entry():
    x()
    y()


def x():
    t()


def y():
    t()


def t():
    return 1
"""


@pytest.fixture
def diamond_tree(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "diamond.py").write_text(DIAMOND_SRC, encoding="utf-8")
    entries = tmp_path / "entries.txt"
    entries.write_text("diamond.py:entry\n", encoding="utf-8")
    return str(root), str(entries)


def _run_query(root, entries, seed):
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    proc = subprocess.run(
        [sys.executable, CLI, "query", "--target", "diamond.py:t",
         "--root", root, "--entries-file", entries],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_call_chain_does_not_depend_on_hash_seed(diamond_tree):
    root, entries = diamond_tree
    outputs = {seed: _run_query(root, entries, seed) for seed in range(1, 9)}

    assert len(set(outputs.values())) == 1, "query output changed with PYTHONHASHSEED"
    # Neighbours are visited in sorted order, so the first-found chain always
    # goes through x (alphabetically before y).
    only = next(iter(outputs.values()))
    chain_lines = [ln for ln in only.splitlines() if "**REACHABLE**" in ln and "diamond.py:entry" in ln]
    assert chain_lines, only
    assert "diamond.py:x (function) -[A]-> " in chain_lines[0]
    assert "diamond.py:y (function)" not in chain_lines[0]
