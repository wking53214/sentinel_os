"""
Acceptance tests -- regression tests against facts about the sentinel_os
repo verified by hand this session, BEFORE this tool existed:

  1. contract_attestation.py, contract_egress.py, contract_retention.py
     are not constructed by any of the five real entry points
     (confirmed: contract_attestation.py is imported only by
     Tests/test_contract_attestation.py; contract_egress.py only by
     contract_attestation.py itself and its own test; contract_retention.py
     only by contract_attestation.py and its own test -- none of those
     importers are production entry points).
  2. regulatory_deck.RegulatoryDeck IS constructed on the live mortgage
     path: sentinel_worker.main() -> _regulatory_deck_from_env() ->
     RegulatoryDeck(...) (confirmed by reading sentinel_worker.py and
     regulatory_deck.py directly, and by git log showing the commits
     that wired this in: 6357f28, f8a407e, 010478f).
  3. The Dockerfile CMD is `python3 api_server_resilient.py`, and
     neither docker-compose.yml, docker-compose-prod.yml, nor
     k8s/deployment.yaml override it with a different command --
     confirmed by reading all four files directly. sentinel_worker.py
     and api_server_v2.py are real, valid entry points on their own,
     but are not what a deployed container actually runs.
  4. gallm_coordinator.py was removed from the repo (git log:
     "Remove dead gallm_coordinator.py and its self-referencing test",
     ede912c) -- only a stale .pyc remains. It must not exist anywhere
     in the parsed source tree.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.dirname(_HERE)
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from model import Graph  # noqa: E402
import reachability as rc  # noqa: E402
import deploy_config as dc  # noqa: E402

REPO_SRC_ROOT = os.environ.get(
    "SENTINEL_OS_SRC_ROOT",
    os.path.expanduser("~/sentinel_os/sentinel_os"),
)

ENTRY_FILES = [
    "governance_harness.py",
    "production_harness.py",
    "sentinel_worker.py",
    "api_server_v2.py",
    "api_server_resilient.py",
]


@pytest.fixture(scope="module")
def graph():
    assert os.path.isdir(REPO_SRC_ROOT), f"expected sentinel_os source root at {REPO_SRC_ROOT}"
    g = Graph(REPO_SRC_ROOT)
    g.build()
    return g


@pytest.fixture(scope="module")
def entries(graph):
    resolved = {spec: rc.resolve_entry_point(graph, spec) for spec in ENTRY_FILES}
    for spec, e in resolved.items():
        assert e.resolved, f"entry point {spec} failed to resolve: {e.note}"
    return resolved


@pytest.mark.parametrize(
    "contract_module",
    ["contract_attestation.py", "contract_egress.py", "contract_retention.py"],
)
def test_contract_modules_unreachable_from_all_entry_points(graph, entries, contract_module):
    mod = graph.modules_by_relpath.get(contract_module)
    assert mod is not None, f"{contract_module} should exist in the parsed tree"

    for spec, entry in entries.items():
        result = rc.check_target(graph, entry.root_ids, mod.top_level_id)
        assert result.status == rc.UNREACHABLE, (
            f"{contract_module} should be UNREACHABLE from {spec}, "
            f"got {result.status}: {result.detail} chain={result.chain}"
        )

    # Every class defined in the module, not just the module pseudo-node.
    for cls_id, cls in graph.classes.items():
        if cls.relpath != contract_module:
            continue
        for spec, entry in entries.items():
            result = rc.check_target(graph, entry.root_ids, cls_id)
            assert result.status == rc.UNREACHABLE, (
                f"{cls.qualname} in {contract_module} should be UNREACHABLE from {spec}, "
                f"got {result.status}: {result.detail} chain={result.chain}"
            )


def test_regulatory_deck_reachable_from_sentinel_worker_main(graph):
    matches = graph.find_by_name("regulatory_deck.RegulatoryDeck")
    assert matches, "RegulatoryDeck should resolve to at least one node"
    target_id = matches[0]

    entry = rc.resolve_entry_point(graph, "sentinel_worker.py:main")
    assert entry.resolved, entry.note

    result = rc.check_target(graph, entry.root_ids, target_id)
    assert result.status == rc.REACHABLE, f"expected REACHABLE, got {result.status}: {result.detail}"
    assert any("_regulatory_deck_from_env" in n for n in result.chain), (
        f"expected the chain to pass through _regulatory_deck_from_env, got: "
        f"{[graph.node_label(n) for n in result.chain]}"
    )


def test_deployed_entry_point_is_api_server_resilient(graph):
    report = dc.detect_deployed_entry_points(REPO_SRC_ROOT)
    assert report.dockerfile_cmd is not None, "Dockerfile CMD should be found"
    assert report.dockerfile_cmd.py_file == "api_server_resilient.py"

    deployed_files = report.deployed_py_files()
    assert "api_server_resilient.py" in deployed_files
    assert "sentinel_worker.py" not in deployed_files
    assert "api_server_v2.py" not in deployed_files

    deployed_entry = rc.resolve_entry_point(graph, "api_server_resilient.py")
    assert deployed_entry.resolved

    for other_spec in ("sentinel_worker.py", "api_server_v2.py"):
        other_entry = rc.resolve_entry_point(graph, other_spec)
        assert other_entry.resolved, f"{other_spec} should still be a VALID standalone entry point on its own"
        assert other_spec not in deployed_files, (
            f"{other_spec} is a valid entry point but must NOT be reported as part of what's deployed"
        )


def test_gallm_coordinator_not_found(graph):
    matches = graph.find_by_name("gallm_coordinator")
    assert matches == [], f"gallm_coordinator should not exist anywhere in the tree, found: {matches}"
    assert "gallm_coordinator.py" not in graph.modules_by_relpath
