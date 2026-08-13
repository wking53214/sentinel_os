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
  3. As of the "Wire the governed mortgage lane into docker-compose"
     commit (669796f), the Dockerfile's own CMD is
     `python3 api_server_v2.py`, and docker-compose.yml runs THREE
     separate entry points as three services, each with its own
     explicit `command:` override: `iceberg` -> api_server_resilient.py
     (the pre-existing IVR-era server, named explicitly since it no
     longer matches the Dockerfile's bare default), `ingress` ->
     api_server_v2.py, `worker` -> sentinel_worker.py. All three are
     real, valid entry points on their own AND all three are actually
     deployed -- confirmed by reading docker-compose.yml directly and
     by running deploy_config.detect_deployed_entry_points() against
     this repo. (k8s/deployment.yaml still has no command override of
     its own, so it inherits the Dockerfile's api_server_v2.py default
     -- untouched here, tracked separately.)
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


def test_deployed_entry_points_match_compose_services(graph):
    report = dc.detect_deployed_entry_points(REPO_SRC_ROOT)
    assert report.dockerfile_cmd is not None, "Dockerfile CMD should be found"
    assert report.dockerfile_cmd.py_file == "api_server_v2.py"

    deployed_files = set(report.deployed_py_files())
    expected = {"api_server_resilient.py", "api_server_v2.py", "sentinel_worker.py"}
    assert deployed_files == expected, (
        f"expected docker-compose.yml's three services to deploy exactly {expected}, got {deployed_files}"
    )

    by_source = {e.source: e.py_file for e in report.entries}
    assert by_source.get("docker-compose.yml:service=iceberg") == "api_server_resilient.py"
    assert by_source.get("docker-compose.yml:service=ingress") == "api_server_v2.py"
    assert by_source.get("docker-compose.yml:service=worker") == "sentinel_worker.py"

    for spec in expected:
        entry = rc.resolve_entry_point(graph, spec)
        assert entry.resolved, f"{spec} should resolve as a real entry point: {entry.note}"


def test_gallm_coordinator_not_found(graph):
    matches = graph.find_by_name("gallm_coordinator")
    assert matches == [], f"gallm_coordinator should not exist anywhere in the tree, found: {matches}"
    assert "gallm_coordinator.py" not in graph.modules_by_relpath
