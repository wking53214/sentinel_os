#!/usr/bin/env python3
"""
wiring_verify -- static wiring-verification CLI for sentinel_os.

See README.md for what this tool can and cannot answer. Short version:
it answers "is there a resolvable static call path from entry point X
to target Y", never "does Y actually execute at runtime" -- those are
the same question only when there is no dynamic dispatch anywhere on
the path, and this tool tells you plainly when it can't be sure.

Usage:
  wiring_verify.py query --target <spec> [--root PATH] [--entries-file PATH]
  wiring_verify.py sweep [--deployed] [--root PATH] [--entries-file PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from model import Graph  # noqa: E402
import reachability as rc  # noqa: E402
import deploy_config as dc  # noqa: E402
import coverage as cov  # noqa: E402

DEFAULT_ENTRIES_FILE = os.path.join(_HERE, "default_entry_points.txt")


def load_entries_file(path: str):
    specs = []
    if not os.path.isfile(path):
        return specs
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    return specs


def build_graph(root: str) -> Graph:
    g = Graph(root)
    g.build()
    return g


def deployed_entry_points(graph: Graph, root: str):
    """Runs deploy_config detection and resolves each detected .py file
    into an EntryPoint against the parsed graph. Returns
    (DeployReport, List[EntryPoint])."""
    report = dc.detect_deployed_entry_points(root)
    eps = []
    for py in report.deployed_py_files():
        candidate = py if py in graph.modules_by_relpath else os.path.basename(py)
        if candidate not in graph.modules_by_relpath:
            continue
        eps.append(rc.resolve_entry_point(graph, candidate))
    return report, eps


# ---------------------------------------------------------------------------
# query mode
# ---------------------------------------------------------------------------

def cmd_query(args) -> int:
    graph = build_graph(args.root)
    entry_specs = load_entries_file(args.entries_file)
    entries = [rc.resolve_entry_point(graph, s) for s in entry_specs]

    matches = graph.find_by_name(args.target)
    lines = []
    lines.append(f"# Wiring query: `{args.target}`\n")
    lines.append(f"Repo source root: `{args.root}`\n")

    if not matches:
        lines.append(f"**NOT_FOUND** -- no module, class, function, or method matching "
                      f"`{args.target}` exists anywhere in the parsed source tree "
                      f"({len(graph.modules_by_relpath)} files parsed, Tests/ excluded).\n")
        lines.append("This is distinct from UNREACHABLE: UNREACHABLE means the symbol exists "
                      "but no call path was found; NOT_FOUND means the symbol was never even "
                      "defined anywhere this tool looked (e.g. removed code, a typo, or a name "
                      "that only ever existed in documentation).\n")
        print("\n".join(lines))
        return 1

    for target_id in matches:
        lines.append(f"## Target: `{graph.node_label(target_id)}`\n")
        lines.append("| Declared entry point | Status | Chain / detail |")
        lines.append("|---|---|---|")
        for spec, entry in zip(entry_specs, entries):
            if not entry.resolved:
                lines.append(f"| `{spec}` | ENTRY_NOT_FOUND | {entry.note} |")
                continue
            result = rc.check_target(graph, entry.root_ids, target_id)
            chain_str = " -> ".join(graph.node_label(n) for n in result.chain) if result.chain else "(no chain)"
            lines.append(f"| `{spec}` | **{result.status}** | {chain_str}<br>{result.detail} |")
        lines.append("")

        deploy_report, deployed_eps = deployed_entry_points(graph, args.root)
        lines.append("**Reachable from the currently DEPLOYED entry point(s):**\n")
        if not deployed_eps:
            lines.append("_No deployed entry point could be resolved from Dockerfile/compose/k8s configs._\n")
        for entry in deployed_eps:
            result = rc.check_target(graph, entry.root_ids, target_id)
            chain_str = " -> ".join(graph.node_label(n) for n in result.chain) if result.chain else "(no chain)"
            lines.append(f"- `{entry.spec}`: **{result.status}** -- {chain_str}")
            lines.append(f"  - {result.detail}")
        lines.append("")

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# sweep mode
# ---------------------------------------------------------------------------

def cmd_sweep(args) -> int:
    graph = build_graph(args.root)
    lines = []
    lines.append("# Wiring sweep report\n")
    lines.append(f"Repo source root: `{args.root}`  ")
    lines.append(f"Files parsed: {len(graph.modules_by_relpath)}  "
                 f"Functions/methods: {len(graph.functions)}  Classes: {len(graph.classes)}\n")
    if graph.parse_errors:
        lines.append("**Parse errors (these files were skipped):**")
        for relpath, err in graph.parse_errors:
            lines.append(f"- `{relpath}`: {err}")
        lines.append("")

    sections = []
    if args.deployed or not args.entries_file_explicit:
        deploy_report, deployed_eps = deployed_entry_points(graph, args.root)
        deployed_roots = sorted({r for e in deployed_eps for r in e.root_ids})
        sections.append(("DEPLOYED entry point(s)",
                          [e.spec for e in deployed_eps], deployed_roots))
        lines.append("## Deployment config resolution\n")
        if deploy_report.dockerfile_cmd:
            lines.append(f"- Dockerfile: `{' '.join(deploy_report.dockerfile_cmd.argv)}` "
                         f"-> `{deploy_report.dockerfile_cmd.py_file}`")
        for e in deploy_report.entries:
            lines.append(f"- {e.source}: `{' '.join(e.argv)}` -> `{e.py_file}`")
        for u in deploy_report.unresolved:
            lines.append(f"- UNRESOLVED: {u}")
        lines.append("")

    entry_specs = load_entries_file(args.entries_file or DEFAULT_ENTRIES_FILE)
    entries = [rc.resolve_entry_point(graph, s) for s in entry_specs]
    declared_roots = sorted({r for e in entries for r in e.root_ids})
    sections.append(("Any DECLARED entry point (" + ", ".join(entry_specs) + ")",
                      entry_specs, declared_roots))

    lines.append("## Reachability, computed separately per question\n")
    lines.append("(reachable-from-any-declared-entry and reachable-from-what's-actually-deployed "
                 "are different questions -- see README -- so they are never merged below)\n")

    all_status_by_section = {}
    for label, specs, roots in sections:
        status = rc.classify_all(graph, roots)
        all_status_by_section[label] = status
        counts = {rc.REACHABLE: 0, rc.UNVERIFIABLE: 0, rc.UNREACHABLE: 0}
        for s in status.values():
            counts[s] += 1
        lines.append(f"### {label}\n")
        lines.append(f"- REACHABLE: {counts[rc.REACHABLE]}")
        lines.append(f"- UNVERIFIABLE_STATICALLY: {counts[rc.UNVERIFIABLE]}")
        lines.append(f"- UNREACHABLE: {counts[rc.UNREACHABLE]}")
        if args.verbose:
            lines.append("\n<details><summary>Full reachable list</summary>\n")
            for nid, s in sorted(status.items()):
                if s == rc.REACHABLE:
                    lines.append(f"- {graph.node_label(nid)}")
            lines.append("\n</details>\n")
        lines.append("")

    lines.append("## Orphans (test-covered, but UNREACHABLE from any declared entry point)\n")
    tested = cov.collect_tested_node_ids(graph, args.root)
    declared_status = all_status_by_section.get(sections[-1][0], {})
    orphans = sorted(nid for nid in tested if declared_status.get(nid) == rc.UNREACHABLE)
    if not orphans:
        lines.append("_None found._\n")
    else:
        by_module = {}
        for nid in orphans:
            relpath = nid.split("::")[0]
            by_module.setdefault(relpath, []).append(nid)
        for relpath in sorted(by_module):
            lines.append(f"- `{relpath}`")
            for nid in sorted(by_module[relpath]):
                lines.append(f"  - {graph.node_label(nid)}")
    lines.append("")

    lines.append(f"## Dynamic call sites (informational, {len(graph.dynamic_sites)} total)\n")
    lines.append("These are call sites this tool could not resolve statically -- getattr with a "
                 "computed name, dict/list-indexed dispatch, calls on a value whose type isn't "
                 "tracked, etc. Not errors; see README for why they can't be resolved.\n")
    if args.verbose:
        for site in graph.dynamic_sites[:200]:
            lines.append(f"- `{site.caller_id}` line {site.lineno}: {site.reason} -- `{site.snippet}`")
        if len(graph.dynamic_sites) > 200:
            lines.append(f"- ... and {len(graph.dynamic_sites) - 200} more (use a target-scoped query for detail)")

    out = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Report written to {args.out}")
    else:
        print(out)
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="wiring_verify", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    q = sub.add_parser("query", help="reachability + call chain for one target, from every declared entry point")
    q.add_argument("--target", required=True, help="e.g. 'regulatory_deck.RegulatoryDeck', "
                                                     "'sentinel_worker.py:main', or a bare name")
    q.add_argument("--root", required=True, help="path to the Python source root to analyze")
    q.add_argument("--entries-file", default=DEFAULT_ENTRIES_FILE)
    q.set_defaults(func=cmd_query)

    s = sub.add_parser("sweep", help="full reachable set + orphan list")
    s.add_argument("--root", required=True)
    s.add_argument("--deployed", action="store_true", help="also resolve entry point(s) from Dockerfile/compose/k8s")
    s.add_argument("--entries-file", default=None)
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--out", default=None, help="write report to this file instead of stdout")
    s.set_defaults(func=cmd_sweep)

    args = parser.parse_args()
    args.root = os.path.abspath(args.root)
    if args.mode == "sweep":
        args.entries_file_explicit = args.entries_file is not None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
