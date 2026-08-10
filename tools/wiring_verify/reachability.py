"""
Reachability computation over the graph built by model.py.

Four possible answers for "is TARGET reachable from ENTRY", and the
tool never collapses them into a bare yes/no:

  REACHABLE               a confirmed static call chain exists (or the
                           target IS itself one of the entry's declared
                           roots -- see below).
  UNVERIFIABLE_STATICALLY the target is only reachable through something
                           this tool cannot resolve statically: a
                           getattr(obj, "literal_name") candidate edge
                           from a node that IS reachable, or a decorator
                           (route registration, plugin hook, etc.) that
                           implies a real caller living outside this
                           repo's static call graph.
  UNREACHABLE              no static path and no unverifiable path either.
  NOT_FOUND                the target string doesn't match anything in
                           the parsed source tree at all -- distinct from
                           UNREACHABLE, which means "exists, but no path".

Entry-point root semantics (see README for the full rationale): a
function-scoped entry (`module.py:func`) has exactly one root, that
function, and only genuine call edges count. A module-scoped entry
(`module.py`, no `:func`) has no single known "first thing called"
without a runtime harness, so its root set is deliberately
over-inclusive: `main()` if present, every public top-level function,
every public method of every public top-level class, and the module's
own top-level statements (which includes the `if __name__ == "__main__"`
body, i.e. exactly what running the file as a script executes). This
is documented, not hidden: over-inclusion produces false REACHABLE
claims in the direction of "this thing is safe/wired" being wrong only
if the module itself is never actually run that way -- the tool always
shows *why* something is reachable (a real chain, vs. "is a root of the
entry module's public surface with no confirmed caller"), so it can't
be misquoted as a proven call chain when it isn't one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from model import Graph, func_node_id

REACHABLE = "REACHABLE"
UNREACHABLE = "UNREACHABLE"
UNVERIFIABLE = "UNVERIFIABLE_STATICALLY"
NOT_FOUND = "NOT_FOUND"


@dataclass
class EntryPoint:
    spec: str
    root_ids: List[str] = field(default_factory=list)
    resolved: bool = True
    note: str = ""


def resolve_entry_point(graph: Graph, spec: str) -> EntryPoint:
    spec = spec.strip()
    if ":" in spec:
        relpath, _, func_spec = spec.partition(":")
        relpath = relpath if relpath.endswith(".py") else relpath + ".py"
        mod = graph.modules_by_relpath.get(relpath)
        if mod is None:
            return EntryPoint(spec=spec, resolved=False, note=f"module '{relpath}' not found in parsed tree")
        fid = func_node_id(relpath, func_spec)
        if fid in graph.functions:
            return EntryPoint(spec=spec, root_ids=[fid])
        # try a class as the func_spec (e.g. module.py:SomeClass -> all its public methods)
        cid = fid  # class_node_id has the same shape as func_node_id
        if cid in graph.classes:
            cls = graph.classes[cid]
            roots = [m for m in cls.methods.values() if graph.functions[m].is_public]
            return EntryPoint(spec=spec, root_ids=roots)
        return EntryPoint(spec=spec, resolved=False, note=f"'{func_spec}' not found in {relpath}")

    relpath = spec if spec.endswith(".py") else spec + ".py"
    mod = graph.modules_by_relpath.get(relpath)
    if mod is None:
        return EntryPoint(spec=spec, resolved=False, note=f"module '{relpath}' not found in parsed tree")

    roots: Set[str] = {mod.top_level_id}
    if "main" in mod.functions:
        roots.add(mod.functions["main"])
    for fid in mod.functions.values():
        if graph.functions[fid].is_public:
            roots.add(fid)
    for cid in mod.classes.values():
        cls = graph.classes[cid]
        if not cls.is_public:
            continue
        for mid in cls.methods.values():
            if graph.functions[mid].is_public:
                roots.add(mid)
    return EntryPoint(spec=spec, root_ids=sorted(roots))


@dataclass
class ReachabilityResult:
    status: str
    chain: List[str] = field(default_factory=list)  # node ids, root..target
    detail: str = ""


def _bfs(graph: Graph, roots: List[str], use_dynamic: bool) -> Dict[str, List[str]]:
    """Returns {node_id: chain-from-a-root-to-node_id} for every node
    reachable from `roots`. When use_dynamic is True, dynamic_candidate
    edges are also traversed (used only for the UNVERIFIABLE_STATICALLY
    check, never to claim a confirmed REACHABLE)."""
    chains: Dict[str, List[str]] = {}
    queue: List[str] = []
    for r in roots:
        if r not in chains:
            chains[r] = [r]
            queue.append(r)
    head = 0
    while head < len(queue):
        cur = queue[head]
        head += 1
        neighbors = set(graph.edges.get(cur, ()))
        if use_dynamic:
            neighbors |= graph.dynamic_candidates.get(cur, set())
        for nxt in neighbors:
            if nxt not in chains:
                chains[nxt] = chains[cur] + [nxt]
                queue.append(nxt)
    return chains


def reachable_set(graph: Graph, roots: List[str], use_dynamic: bool = False) -> Set[str]:
    return set(_bfs(graph, roots, use_dynamic).keys())


def classify_all(graph: Graph, roots: List[str]) -> Dict[str, str]:
    """Status for every function/method/class node against one root set,
    computing the two BFS passes ONCE and reusing target_aliases so a
    class shows REACHABLE whenever its __init__ does (a class is only
    ever a Call *target* in the graph via its constructor -- see
    target_aliases) instead of raw node-id membership, which would
    misreport every constructed-but-never-directly-referenced class as
    UNREACHABLE."""
    confirmed = reachable_set(graph, roots, use_dynamic=False)
    with_dynamic = reachable_set(graph, roots, use_dynamic=True)
    status: Dict[str, str] = {}
    for nid, fn in graph.functions.items():
        if nid in confirmed:
            status[nid] = REACHABLE
        elif nid in with_dynamic or fn.dynamically_registered:
            status[nid] = UNVERIFIABLE
        else:
            status[nid] = UNREACHABLE
    for nid in graph.classes:
        aliases = target_aliases(graph, nid)
        if any(a in confirmed for a in aliases):
            status[nid] = REACHABLE
        elif any(a in with_dynamic for a in aliases):
            status[nid] = UNVERIFIABLE
        else:
            status[nid] = UNREACHABLE
    return status


def target_aliases(graph: Graph, target_id: str) -> List[str]:
    """A class is only ever a Call *target* in the graph via its
    __init__ (see model.py's _edge_to_constructor: a constructor call
    adds an edge to __init__, or to the class node itself only when
    there's no explicit __init__). Querying the class by name must
    still count a confirmed construction as reachable, so both ids are
    checked."""
    aliases = [target_id]
    cls = graph.classes.get(target_id)
    if cls and "__init__" in cls.methods:
        aliases.append(cls.methods["__init__"])
    return aliases


def check_target(graph: Graph, roots: List[str], target_id: str) -> ReachabilityResult:
    if not roots:
        return ReachabilityResult(status=UNREACHABLE, detail="entry point has no resolvable roots")

    aliases = target_aliases(graph, target_id)
    confirmed = _bfs(graph, roots, use_dynamic=False)
    hit = next((a for a in aliases if a in confirmed), None)
    if hit:
        chain = confirmed[hit]
        if len(chain) == 1:
            fn = graph.functions.get(target_id)
            if fn and fn.dynamically_registered:
                return ReachabilityResult(
                    status=REACHABLE, chain=chain,
                    detail=("target is part of the entry point's own declared public surface; "
                            f"NO confirmed static caller was found for it -- {fn.dyn_reason}"),
                )
            return ReachabilityResult(
                status=REACHABLE, chain=chain,
                detail="target IS one of the entry point's declared roots (no call chain needed)",
            )
        return ReachabilityResult(status=REACHABLE, chain=chain, detail="confirmed static call chain")

    with_dynamic = _bfs(graph, roots, use_dynamic=True)
    hit = next((a for a in aliases if a in with_dynamic), None)
    if hit:
        return ReachabilityResult(
            status=UNVERIFIABLE, chain=with_dynamic[hit],
            detail="only reachable through a dynamic-dispatch candidate edge (e.g. getattr with a literal "
                   "name) -- not a confirmed static call; may or may not execute at runtime",
        )

    fn = graph.functions.get(target_id)
    if fn and fn.dynamically_registered:
        return ReachabilityResult(
            status=UNVERIFIABLE, chain=[],
            detail=f"no static or dynamic-candidate path found, but the target itself is decorator-registered: {fn.dyn_reason}",
        )

    return ReachabilityResult(status=UNREACHABLE, chain=[], detail="no static call path and no dynamic-candidate path found")
