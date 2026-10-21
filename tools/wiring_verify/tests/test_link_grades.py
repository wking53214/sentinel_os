"""
Every call link carries a grade, so a reader can see how strong each step of a
chain is, not only the verdict for the whole chain.

  A  caller and callee are in the same file (resolved directly)
  B  callee is in another file, reached through an import or a tracked type
  U  candidate only: a getattr(obj, "literal") guess, never confirmed

A chain is REACHABLE only when every link is A or B. A chain with a U link is
UNVERIFIABLE_STATICALLY. These tests pin the grade of each link.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from model import Graph, func_node_id
import reachability as rc

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.dirname(_HERE)
CLI = os.path.join(_TOOL_DIR, "cli.py")

A_SRC = """\
def g():
    return 1


def f():
    g()
"""

B_SRC = """\
from a import g


def h():
    g()
"""

DYN_SRC = """\
def dyn_entry():
    getattr(object(), "target_fn")()


def target_fn():
    return 2
"""


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.py").write_text(A_SRC, encoding="utf-8")
    (root / "b.py").write_text(B_SRC, encoding="utf-8")
    (root / "dyn.py").write_text(DYN_SRC, encoding="utf-8")
    return str(root)


@pytest.fixture
def graph(tree):
    g = Graph(tree)
    g.build()
    return g


def test_same_file_call_is_grade_A(graph):
    assert graph.edge_grades[(func_node_id("a.py", "f"), func_node_id("a.py", "g"))] == "A"


def test_cross_file_call_through_import_is_grade_B(graph):
    assert graph.edge_grades[(func_node_id("b.py", "h"), func_node_id("a.py", "g"))] == "B"


def test_candidate_only_link_is_grade_U(graph):
    link = (func_node_id("dyn.py", "dyn_entry"), func_node_id("dyn.py", "target_fn"))
    assert link not in graph.edge_grades
    assert link[1] in graph.dynamic_candidates[link[0]]
    assert rc.link_grade(graph, *link) == "U"


def test_reachable_chain_has_only_confirmed_links(graph):
    entry = rc.resolve_entry_point(graph, "b.py:h")
    result = rc.check_target(graph, entry.root_ids, func_node_id("a.py", "g"))
    assert result.status == rc.REACHABLE
    assert [rc.link_grade(graph, a, b) for a, b in zip(result.chain, result.chain[1:])] == ["B"]


def test_chain_through_candidate_is_unverifiable_and_marks_U(graph):
    entry = rc.resolve_entry_point(graph, "dyn.py:dyn_entry")
    result = rc.check_target(graph, entry.root_ids, func_node_id("dyn.py", "target_fn"))
    assert result.status == rc.UNVERIFIABLE
    assert [rc.link_grade(graph, a, b) for a, b in zip(result.chain, result.chain[1:])] == ["U"]


def test_grade_counts_cover_confirmed_and_candidate_links(graph):
    counts = rc.grade_counts(graph)
    assert counts["A"] >= 1
    assert counts["B"] >= 1
    assert counts["U"] == 1


def test_query_output_shows_grade_on_each_link(tmp_path, tree):
    entries = tmp_path / "entries.txt"
    entries.write_text("b.py:h\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, CLI, "query", "--target", "a.py:g", "--root", tree, "--entries-file", str(entries)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "-[B]->" in proc.stdout
    assert "Link grades:" in proc.stdout
