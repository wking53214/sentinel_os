"""
Test-coverage detection, kept deliberately SEPARATE from the main call
graph in model.py.

Why separate: the whole point of this tool is telling "tested" apart
from "wired into anything that runs" (COMPLIANCE.md's own recurring
defect shape -- "real, fully-tested code that no entrypoint ever
constructs"). If a test file's calls were folded into the same graph
used for reachability, a well-tested-but-dead module would falsely
show up as REACHABLE (reachable from its own test), which defeats the
tool's purpose. So: production reachability comes only from model.py's
graph (Tests/ excluded at parse time, per the spec), and this module
separately records which production symbols any test file *mentions*,
purely as metadata used for the orphan report in reachability.py's
consumer (cli.py) -- never as a graph edge.

Best-effort, not exhaustive: resolves direct imports and attribute
access off them. Does not do the local-variable/self-attribute type
inference model.py does for the real graph -- a test that does
`obj = SomeClass(); obj.method()` is credited with testing SomeClass
(the constructor call resolves) but not necessarily .method specifically
unless the test also imports/references it some other way. This is a
coverage *signal*, not a coverage report a compliance team should cite
numbers from.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from model import Graph, flatten_attr_chain, relpath_to_dotted, EXCLUDED_DIR_NAMES

TEST_ONLY_EXCLUDED = {n for n in EXCLUDED_DIR_NAMES if n != "Tests"}


def find_test_files(root: str) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in TEST_ONLY_EXCLUDED and not d.startswith(".")]
        in_tests_dir = "Tests" in dirpath.split(os.sep)
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if in_tests_dir or fn.startswith("test_") or fn.endswith("_test.py"):
                out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


@dataclass
class _TestImport:
    module_dotted: str
    attr: Optional[str]  # None if the import binds the whole module


def _resolve_relative_base(test_relpath: str, module: Optional[str], level: int) -> str:
    parts = test_relpath[:-3].split(os.sep)
    is_init = parts and parts[-1] == "__init__"
    pkg_parts = parts[:-1] if not is_init else parts
    up = level - 1
    if up:
        pkg_parts = pkg_parts[: len(pkg_parts) - up] if up < len(pkg_parts) else []
    base = ".".join(pkg_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def collect_tested_node_ids(graph: Graph, root: str) -> Set[str]:
    tested: Set[str] = set()
    for relpath in find_test_files(root):
        full = os.path.join(root, relpath)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read(), filename=relpath)
        except (SyntaxError, OSError):
            continue

        imports: Dict[str, _TestImport] = {}
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    local = alias.asname or alias.name.split(".")[0]
                    imports[local] = _TestImport(module_dotted=alias.name.split(".")[0] if not alias.asname else alias.name, attr=None)
            elif isinstance(stmt, ast.ImportFrom):
                base = _resolve_relative_base(relpath, stmt.module, stmt.level)
                for alias in stmt.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    if base in graph.modules:
                        imports[local] = _TestImport(module_dotted=base, attr=alias.name)
                    elif f"{base}.{alias.name}" in graph.modules:
                        imports[local] = _TestImport(module_dotted=f"{base}.{alias.name}", attr=None)

        def resolve_chain(chain: List[str]) -> Optional[str]:
            head, rest = chain[0], chain[1:]
            binding = imports.get(head)
            if binding is None:
                return None
            if binding.attr is None:
                # local name IS a module
                mod_dotted = binding.module_dotted
                if not rest:
                    return graph.modules[mod_dotted].top_level_id if mod_dotted in graph.modules else None
                if mod_dotted in graph.modules:
                    m = graph.modules[mod_dotted]
                    leaf = rest[0]
                    if leaf in m.classes:
                        return m.classes[leaf]
                    if leaf in m.functions:
                        return m.functions[leaf]
                return None
            # binding is one attribute of a known module
            m = graph.modules.get(binding.module_dotted)
            if m is None:
                return None
            if binding.attr in m.classes:
                cls_id = m.classes[binding.attr]
                if not rest:
                    return cls_id
                method = rest[0]
                cls = graph.classes[cls_id]
                if method in cls.methods:
                    tested.add(cls_id)  # using a method also counts as testing the class
                    return cls.methods[method]
                return cls_id
            if binding.attr in m.functions and not rest:
                return m.functions[binding.attr]
            return None

        for node in ast.walk(tree):
            chain = None
            if isinstance(node, ast.Name):
                chain = [node.id]
            elif isinstance(node, ast.Attribute):
                chain = flatten_attr_chain(node)
            elif isinstance(node, ast.Call):
                continue  # handled via its .func Name/Attribute child
            if not chain:
                continue
            found = resolve_chain(chain)
            if found:
                tested.add(found)

    return tested
