"""
AST-based call/definition graph for sentinel_os.

Scope and honesty notes (see README.md for the full version):

  * This module answers "is there a resolvable static call path", not
    "does this code run". Anything that depends on runtime values
    (getattr with a computed name, a framework calling a decorated
    handler, importlib, a dict-of-callables dispatch) cannot be proven
    or disproven here -- see UNVERIFIABLE_STATICALLY in reachability.py.
  * Type inference is a single-pass, best-effort heuristic: direct
    local-variable assignment from a constructor call
    (`x = SomeClass(...)`) and direct `self.attr = SomeClass(...)`
    assignment in any method of the owning class. No dataflow across
    branches/loops, no cross-function inference, last assignment wins.
    Anything beyond that (an object handed in as a parameter, an item
    pulled out of a collection, an attribute of unknown provenance)
    resolves to UNRESOLVED, not a guess.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# Directories never treated as source: not shipped, not production code,
# or -- for Tests -- explicitly excluded by the tool's own spec (test
# coverage is tracked separately, see coverage.py, precisely so a test
# calling something can never make it falsely "reachable from production").
EXCLUDED_DIR_NAMES = {
    "Tests", "venv", "__pycache__", ".git", "ledger_data",
    ".pytest_cache", ".ruff_cache", "node_modules", "archive",
    ".mypy_cache", "wiring_verify",
}

# Decorators known not to imply an external/framework call path back into
# the decorated function. Anything else that is a Call-shaped decorator
# (has parens, e.g. `@app.get("/health")`, `@retry(times=3)`) or an
# unrecognized bare/attribute decorator is treated as a sign that this
# function's real caller lives outside this repo's static call graph
# (a route table, a plugin registry, a signal handler, ...).
STRUCTURAL_DECORATORS = {
    "staticmethod", "classmethod", "property", "abstractmethod",
    "abc.abstractmethod", "functools.wraps", "wraps", "contextmanager",
    "contextlib.contextmanager", "overload", "typing.overload",
    "dataclass", "dataclasses.dataclass", "cached_property",
    "functools.cached_property", "final", "typing.final",
}

BUILTIN_NAMES = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))


def relpath_to_dotted(relpath: str) -> str:
    """'cassettes/mortgage_cassette.py' -> 'cassettes.mortgage_cassette'
    '__init__.py' at package root -> the package dir's own dotted name
    (handled by caller trimming the trailing '.__init__')."""
    parts = relpath[:-3].split(os.sep) if relpath.endswith(".py") else relpath.split(os.sep)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def flatten_attr_chain(node: ast.AST) -> Optional[List[str]]:
    """Attribute(Attribute(Name('a'),'b'),'c') -> ['a','b','c'].
    Returns None if the base of the chain is not a plain Name (e.g. it
    starts with a Call or Subscript -- that's a dynamic base, not a
    static dotted path)."""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    return None


def decorator_text(dec: ast.AST) -> str:
    try:
        return ast.unparse(dec)
    except Exception:
        return "<decorator>"


def decorator_key(dec: ast.AST) -> str:
    """Best-effort dotted name of a decorator, stripping any call args:
    `app.get("/x")` -> 'app.get', `staticmethod` -> 'staticmethod'."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    chain = flatten_attr_chain(target)
    if chain:
        return ".".join(chain)
    return decorator_text(dec)


@dataclass
class ImportBinding:
    """What a local name in a module resolves to, syntactically. Whether
    `target_dotted` actually exists in this repo is checked later --
    an import of an external package (os, fastapi, httpx, ...) produces
    a binding too, it just never matches anything in the graph, which is
    exactly the "external, not tracked" case we want (no edge, not an
    error)."""
    kind: str  # "module" (local name IS a module) or "attr" (local name is one symbol from a module)
    target_dotted: str
    attr: Optional[str] = None


@dataclass
class FuncNode:
    id: str
    relpath: str
    qualname: str
    name: str
    is_async: bool
    is_method: bool
    owner_class: Optional[str]  # ClassNode.id, or None for a module-level function
    lineno: int
    decorators: List[str] = field(default_factory=list)
    dynamically_registered: bool = False
    dyn_reason: Optional[str] = None
    ast_node: Optional[ast.AST] = None
    module_dotted: str = ""
    is_public: bool = True


@dataclass
class ClassNode:
    id: str
    relpath: str
    qualname: str
    name: str
    bases_raw: List[str]
    lineno: int
    methods: Dict[str, str] = field(default_factory=dict)  # bare method name -> FuncNode.id
    resolved_bases: List[str] = field(default_factory=list)  # ClassNode.id list, best-effort
    instance_attr_types: Dict[str, str] = field(default_factory=dict)  # attr name -> ClassNode.id
    ast_node: Optional[ast.AST] = None
    module_dotted: str = ""
    is_public: bool = True


@dataclass
class ModuleInfo:
    relpath: str
    dotted: str
    ast_tree: ast.Module
    imports: Dict[str, ImportBinding] = field(default_factory=dict)
    functions: Dict[str, str] = field(default_factory=dict)  # bare name -> FuncNode.id
    classes: Dict[str, str] = field(default_factory=dict)  # bare name -> ClassNode.id
    top_level_id: str = ""
    has_dunder_main: bool = False
    dunder_main_body: List[ast.stmt] = field(default_factory=list)


@dataclass
class DynamicSite:
    caller_id: str
    lineno: int
    reason: str
    snippet: str


def module_node_id(relpath: str) -> str:
    return f"{relpath}::<module>"


def func_node_id(relpath: str, qualname: str) -> str:
    return f"{relpath}::{qualname}"


def class_node_id(relpath: str, qualname: str) -> str:
    return f"{relpath}::{qualname}"


class Graph:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.modules: Dict[str, ModuleInfo] = {}  # dotted -> ModuleInfo
        self.modules_by_relpath: Dict[str, ModuleInfo] = {}
        self.functions: Dict[str, FuncNode] = {}  # id -> FuncNode (module funcs + methods)
        self.classes: Dict[str, ClassNode] = {}  # id -> ClassNode
        self.by_bare_name: Dict[str, List[str]] = defaultdict(list)
        self.edges: Dict[str, Set[str]] = defaultdict(set)
        self.edge_lines: Dict[Tuple[str, str], int] = {}
        self.dynamic_candidates: Dict[str, Set[str]] = defaultdict(set)
        self.dynamic_sites: List[DynamicSite] = []
        self.parse_errors: List[Tuple[str, str]] = []

    # ------------------------------------------------------------------
    # Phase 1: discovery + parse + top-level symbol table
    # ------------------------------------------------------------------

    def discover_files(self) -> List[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES and not d.startswith(".")]
            for fn in filenames:
                if fn.endswith(".py"):
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, self.root)
                    out.append(rel)
        return sorted(out)

    def build(self) -> None:
        relpaths = self.discover_files()
        for rel in relpaths:
            self._parse_module(rel)
        # Second pass needs every module registered first, so import
        # bindings can tell a submodule apart from an attribute.
        for mod in self.modules_by_relpath.values():
            self._resolve_imports(mod)
        for cls in self.classes.values():
            self._resolve_bases(cls)
        for cls in self.classes.values():
            self._infer_instance_attrs(cls)
        for mod in self.modules_by_relpath.values():
            self._resolve_calls_in_module(mod)

    def _parse_module(self, relpath: str) -> None:
        full = os.path.join(self.root, relpath)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            tree = ast.parse(src, filename=relpath)
        except (SyntaxError, OSError) as exc:
            self.parse_errors.append((relpath, str(exc)))
            return

        dotted = relpath_to_dotted(relpath)
        mod = ModuleInfo(relpath=relpath, dotted=dotted, ast_tree=tree,
                          top_level_id=module_node_id(relpath))
        self.modules[dotted] = mod
        self.modules_by_relpath[relpath] = mod
        self.by_bare_name[dotted.split(".")[-1]].append(mod.top_level_id)

        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                self._record_import(mod, stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = self._make_func_node(relpath, dotted, stmt, owner_class=None, qual_prefix="")
                mod.functions[stmt.name] = fn.id
            elif isinstance(stmt, ast.ClassDef):
                self._make_class_node(relpath, dotted, stmt, mod)
            elif isinstance(stmt, ast.If):
                # `if __name__ == "__main__":` -- the one module-level
                # conditional whose body genuinely executes when this
                # file is run as a script (i.e. is a deploy entry point).
                if self._is_dunder_main_guard(stmt):
                    mod.has_dunder_main = True
                    mod.dunder_main_body = stmt.body

    @staticmethod
    def _is_dunder_main_guard(stmt: ast.If) -> bool:
        test = stmt.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            return False
        if not isinstance(test.ops[0], ast.Eq):
            return False
        left, right = test.left, test.comparators[0]

        def is_name_expr(n):
            return isinstance(n, ast.Name) and n.id == "__name__"

        def is_main_str(n):
            return isinstance(n, ast.Constant) and n.value == "__main__"

        return (is_name_expr(left) and is_main_str(right)) or (is_name_expr(right) and is_main_str(left))

    def _make_func_node(self, relpath, module_dotted, node, owner_class: Optional[str], qual_prefix: str) -> FuncNode:
        qualname = f"{qual_prefix}.{node.name}" if qual_prefix else node.name
        nid = func_node_id(relpath, qualname)
        decos = [decorator_key(d) for d in node.decorator_list]
        dyn, reason = self._classify_decorators(node.decorator_list, decos)
        fn = FuncNode(
            id=nid, relpath=relpath, qualname=qualname, name=node.name,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_method=owner_class is not None, owner_class=owner_class,
            lineno=node.lineno, decorators=decos,
            dynamically_registered=dyn, dyn_reason=reason,
            ast_node=node, module_dotted=module_dotted,
            is_public=not node.name.startswith("_") or (node.name.startswith("__") and node.name.endswith("__")),
        )
        self.functions[nid] = fn
        self.by_bare_name[node.name].append(nid)
        return fn

    @staticmethod
    def _classify_decorators(decorator_nodes: List[ast.AST], decos: List[str]) -> Tuple[bool, Optional[str]]:
        for dnode, dkey in zip(decorator_nodes, decos):
            base = dkey.split("(")[0]
            if base in STRUCTURAL_DECORATORS or base.rsplit(".", 1)[-1] in STRUCTURAL_DECORATORS:
                continue
            # Anything else: a Call-shaped decorator (registration with
            # args, e.g. app.get("/health"), retry(times=3)) or an
            # unrecognized bare/attribute decorator not in the allowlist.
            if isinstance(dnode, ast.Call):
                return True, f"decorator '@{decorator_text(dnode)}' is call-shaped and not a known structural decorator -- likely framework/registry dispatch, not a static call site"
            if isinstance(dnode, (ast.Attribute,)):
                return True, f"decorator '@{decorator_text(dnode)}' is not a recognized structural decorator -- treat its target as possibly framework-dispatched"
            # bare Name not in the allowlist (e.g. a project-local
            # registration decorator like @register_handler)
            return True, f"decorator '@{decorator_text(dnode)}' is not a recognized structural decorator -- treat its target as possibly framework/registry-dispatched"
        return False, None

    def _make_class_node(self, relpath, module_dotted, node: ast.ClassDef, mod: ModuleInfo,
                          owner_prefix: str = "") -> ClassNode:
        qualname = f"{owner_prefix}.{node.name}" if owner_prefix else node.name
        nid = class_node_id(relpath, qualname)
        bases_raw = []
        for b in node.bases:
            chain = flatten_attr_chain(b)
            bases_raw.append(".".join(chain) if chain else decorator_text(b))
        cls = ClassNode(
            id=nid, relpath=relpath, qualname=qualname, name=node.name,
            bases_raw=bases_raw, lineno=node.lineno, ast_node=node,
            module_dotted=module_dotted,
            is_public=not node.name.startswith("_"),
        )
        self.classes[nid] = cls
        self.by_bare_name[node.name].append(nid)
        if not owner_prefix:
            mod.classes[node.name] = nid

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = self._make_func_node(relpath, module_dotted, item, owner_class=nid, qual_prefix=qualname)
                cls.methods[item.name] = fn.id
            elif isinstance(item, ast.ClassDef):
                self._make_class_node(relpath, module_dotted, item, mod, owner_prefix=qualname)
        return cls

    def _record_import(self, mod: ModuleInfo, stmt) -> None:
        self._collect_import(mod, stmt, mod.imports)

    def _collect_import(self, mod: ModuleInfo, stmt, target: Dict[str, "ImportBinding"]) -> None:
        """Shared by module-level import recording and the per-function
        local-import prepass (`_ScopePrepass`) -- a local `from x import
        y` inside a function body is just as real a binding as a
        module-level one, and this codebase uses them deliberately
        (e.g. sentinel_worker._regulatory_deck_from_env imports
        RegulatoryDeck inside the function to keep it independently
        testable/importable without a module-level dependency)."""
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                dotted = alias.name
                local = alias.asname or dotted.split(".")[0]
                if alias.asname:
                    target[local] = ImportBinding(kind="module", target_dotted=dotted)
                else:
                    # `import a.b.c` binds name `a`, but any later
                    # `a.b.c.func()` still resolves through this ->
                    # handled by attribute-chain resolution walking
                    # through submodule bindings (see _resolve_attr_call).
                    target[local] = ImportBinding(kind="module", target_dotted=dotted.split(".")[0])
        else:  # ImportFrom
            base = self._resolve_relative_base(mod, stmt.module, stmt.level)
            for alias in stmt.names:
                local = alias.asname or alias.name
                if alias.name == "*":
                    continue
                target_dotted = f"{base}.{alias.name}" if base else alias.name
                # Ambiguous until resolved: could be "from pkg import submodule"
                # (target_dotted IS a module) or "from mod import Symbol"
                # (target_dotted's prefix `base` is the module, alias.name
                # the attribute). Store both candidates; resolved by
                # _resolve_import_bindings once every module is known.
                binding = ImportBinding(kind="unresolved_from", target_dotted=target_dotted, attr=alias.name)
                binding.__dict__["_from_base"] = base
                target[local] = binding

    @staticmethod
    def _resolve_relative_base(mod: ModuleInfo, module: Optional[str], level: int) -> str:
        if level == 0:
            return module or ""
        # level>=1: relative import. mod.dotted's package is everything
        # but the last component (or, for a package __init__, itself).
        pkg_parts = mod.dotted.split(".")
        is_package_init = os.path.basename(mod.relpath) == "__init__.py"
        if not is_package_init:
            pkg_parts = pkg_parts[:-1]
        # level=1 -> current package; level=2 -> parent package; etc.
        up = level - 1
        if up:
            pkg_parts = pkg_parts[: len(pkg_parts) - up] if up < len(pkg_parts) else []
        base_pkg = ".".join(pkg_parts)
        if module:
            return f"{base_pkg}.{module}" if base_pkg else module
        return base_pkg

    def _resolve_imports(self, mod: ModuleInfo) -> None:
        self._resolve_import_bindings(mod.imports)

    def _resolve_import_bindings(self, bindings: Dict[str, "ImportBinding"]) -> None:
        for local, binding in list(bindings.items()):
            if binding.kind != "unresolved_from":
                continue
            from_base = binding.__dict__.get("_from_base", "")
            # Case A: target_dotted itself is a known module in this repo
            # -> "from pkg import submodule" (local name IS that module).
            if binding.target_dotted in self.modules:
                binding.kind = "module"
                binding.attr = None
                continue
            # Case B: from_base is a known module -> local name is one
            # attribute (function/class/variable) defined in it.
            if from_base in self.modules:
                binding.kind = "attr"
                binding.target_dotted = from_base
                binding.attr = binding.attr
                continue
            # Case C: neither resolves inside the repo -> external
            # (stdlib/third-party) import. Leave as "attr" pointing at a
            # dotted path that won't match anything -- calls through it
            # simply won't find a node, which is the correct "external,
            # not tracked" outcome.
            binding.kind = "attr"

    def _resolve_bases(self, cls: ClassNode) -> None:
        mod = self.modules_by_relpath[cls.relpath]
        for base_dotted in cls.bases_raw:
            head, *rest = base_dotted.split(".")
            resolved_module = None
            resolved_name = None
            if head in mod.imports:
                b = mod.imports[head]
                if b.kind == "module":
                    full_mod = b.target_dotted if not rest else ".".join([b.target_dotted] + rest[:-1])
                    name = rest[-1] if rest else None
                    resolved_module, resolved_name = full_mod, name
                elif b.kind == "attr" and not rest:
                    resolved_module, resolved_name = b.target_dotted, b.attr
            elif head in mod.classes and not rest:
                cls.resolved_bases.append(mod.classes[head])
                continue
            elif head == mod.dotted.split(".")[-1] and not rest:
                continue  # base is a builtin/unresolvable single name, skip
            if resolved_module and resolved_module in self.modules and resolved_name:
                target_mod = self.modules[resolved_module]
                if resolved_name in target_mod.classes:
                    cls.resolved_bases.append(target_mod.classes[resolved_name])

    def _infer_instance_attrs(self, cls: ClassNode) -> None:
        """Two sources for `self.attr`'s inferred type, for resolving
        `self.attr.method(...)` call sites elsewhere in the class:

          1. `self.attr = SomeClass(...)` -- a direct constructor call.
          2. `self.attr = param` where `param` is one of the enclosing
             method's own parameters AND has a type annotation
             resolving to a known class (unwrapping `Optional[X]` /
             `Union[X, None]`). This is the common dependency-injection
             constructor pattern (`def __init__(self, x: SomeClass)`) --
             without it, every constructor-injected collaborator looks
             like an untyped attribute and every method call through it
             is reported UNREACHABLE/UNVERIFIABLE with no real basis.

        Both are single-pass, last-write-wins, no cross-method
        reconciliation -- see module docstring."""
        mod = self.modules_by_relpath[cls.relpath]
        for method_id in cls.methods.values():
            fn = self.functions[method_id]
            if fn.ast_node is None:
                continue
            param_types = self._param_annotation_types(mod, fn.ast_node)
            for node in ast.walk(fn.ast_node):
                if not isinstance(node, ast.Assign):
                    continue
                if len(node.targets) != 1:
                    continue
                tgt = node.targets[0]
                if not (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self"):
                    continue
                callee_cls_id = self._resolve_constructor(mod, node.value)
                if not callee_cls_id and isinstance(node.value, ast.Name):
                    callee_cls_id = param_types.get(node.value.id)
                if callee_cls_id:
                    cls.instance_attr_types[tgt.attr] = callee_cls_id

    def _param_annotation_types(self, mod: ModuleInfo, func_ast) -> Dict[str, str]:
        args = func_ast.args
        all_params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        out: Dict[str, str] = {}
        for a in all_params:
            if a.annotation is None:
                continue
            cls_id = self._resolve_annotation_to_class(mod, a.annotation)
            if cls_id:
                out[a.arg] = cls_id
        return out

    def _resolve_annotation_to_class(self, mod: ModuleInfo, annotation: ast.AST) -> Optional[str]:
        # String forward-ref annotation, e.g. `x: "RegulatoryDeck"`.
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return None
            return self._resolve_annotation_to_class(mod, parsed)
        # Optional[X] / Union[X, None] / X | None -> unwrap to X.
        if isinstance(annotation, ast.Subscript):
            head_chain = flatten_attr_chain(annotation.value)
            head = head_chain[-1] if head_chain else None
            if head == "Optional":
                return self._resolve_annotation_to_class(mod, annotation.slice)
            if head == "Union":
                elts = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
                for elt in elts:
                    if isinstance(elt, ast.Constant) and elt.value is None:
                        continue
                    resolved = self._resolve_annotation_to_class(mod, elt)
                    if resolved:
                        return resolved
            return None
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            for side in (annotation.left, annotation.right):
                if isinstance(side, ast.Constant) and side.value is None:
                    continue
                resolved = self._resolve_annotation_to_class(mod, side)
                if resolved:
                    return resolved
            return None
        chain = flatten_attr_chain(annotation)
        if not chain:
            return None
        return self._resolve_dotted_to_class(mod, chain)

    def _resolve_constructor(self, mod: ModuleInfo, value: ast.AST) -> Optional[str]:
        if not isinstance(value, ast.Call):
            return None
        chain = flatten_attr_chain(value.func)
        if not chain:
            return None
        return self._resolve_dotted_to_class(mod, chain)

    def _resolve_dotted_to_class(self, mod: ModuleInfo, chain: List[str]) -> Optional[str]:
        head, rest = chain[0], chain[1:]
        if head in mod.classes and not rest:
            return mod.classes[head]
        if head in mod.imports:
            b = mod.imports[head]
            if b.kind == "attr" and not rest and b.target_dotted in self.modules:
                target_mod = self.modules[b.target_dotted]
                if b.attr in target_mod.classes:
                    return target_mod.classes[b.attr]
            if b.kind == "module":
                full = b.target_dotted if not rest else ".".join([b.target_dotted] + rest[:-1])
                name = rest[-1] if rest else None
                if full in self.modules and name and name in self.modules[full].classes:
                    return self.modules[full].classes[name]
        return None

    # ------------------------------------------------------------------
    # Phase 2: call resolution
    # ------------------------------------------------------------------

    def _resolve_calls_in_module(self, mod: ModuleInfo) -> None:
        for fn_id in list(mod.functions.values()):
            self._resolve_calls_in_func(mod, self.functions[fn_id])
        for cls_id in mod.classes.values():
            cls = self.classes[cls_id]
            for method_id in cls.methods.values():
                self._resolve_calls_in_func(mod, self.functions[method_id], owner_cls=cls)
        # Module top-level statements (executed at import time), plus the
        # `if __name__ == "__main__":` body (executed only when run as a
        # script -- exactly the deploy-time entry path).
        top_stmts = [s for s in mod.ast_tree.body
                     if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                            ast.Import, ast.ImportFrom))]
        self._walk_calls(mod, mod.top_level_id, top_stmts, owner_cls=None)
        if mod.dunder_main_body:
            self._walk_calls(mod, mod.top_level_id, mod.dunder_main_body, owner_cls=None)

    def _resolve_calls_in_func(self, mod: ModuleInfo, fn: FuncNode, owner_cls: Optional[ClassNode] = None) -> None:
        if fn.ast_node is None:
            return
        self._walk_calls(mod, fn.id, fn.ast_node.body, owner_cls=owner_cls)

    def _walk_calls(self, mod: ModuleInfo, caller_id: str, stmts: List[ast.stmt],
                     owner_cls: Optional[ClassNode]) -> None:
        prepass = _ScopePrepass(self, mod)
        for s in stmts:
            prepass.visit(s)
        self._resolve_import_bindings(prepass.local_imports)
        effective_imports = dict(mod.imports)
        effective_imports.update(prepass.local_imports)  # function-local imports shadow module-level

        visitor = _CallCollector(self, mod, caller_id, owner_cls, prepass.local_types, effective_imports)
        for s in stmts:
            visitor.visit(s)

    def add_edge(self, caller_id: str, callee_id: str, lineno: int) -> None:
        self.edges[caller_id].add(callee_id)
        self.edge_lines.setdefault((caller_id, callee_id), lineno)

    def add_dynamic_candidate(self, caller_id: str, callee_id: str) -> None:
        self.dynamic_candidates[caller_id].add(callee_id)

    # ------------------------------------------------------------------
    # Lookup helpers used by the CLI
    # ------------------------------------------------------------------

    def find_by_name(self, spec: str) -> List[str]:
        """Best-effort resolution of a human-typed target spec to node
        ids. Tries, in order: exact node id, 'relpath:qualname',
        'dotted.module.Qualname', then falls back to bare-name search
        across the whole tree (functions, methods, classes, modules)."""
        if spec in self.functions or spec in self.classes:
            return [spec]
        if "::" in spec:
            return [spec] if (spec in self.functions or spec in self.classes) else []
        if spec.endswith(".py") and spec in self.modules_by_relpath:
            return [self.modules_by_relpath[spec].top_level_id]

        if ":" in spec:
            relpart, _, qual = spec.partition(":")
            relpart = relpart if relpart.endswith(".py") else relpart + ".py"
            candidates = [k for k in list(self.functions) + list(self.classes)
                          if k.startswith(relpart + "::")]
            exact = [k for k in candidates if k == func_node_id(relpart, qual) or k == class_node_id(relpart, qual)]
            if exact:
                return exact
            suffix = [k for k in candidates if k.endswith("::" + qual) or f"::{qual}." in k or k.split("::")[-1].split(".")[-1] == qual]
            return suffix

        # dotted form: pkg.module.Symbol or pkg.module
        if "." in spec:
            mod_part, _, sym = spec.rpartition(".")
            if mod_part in self.modules:
                m = self.modules[mod_part]
                if sym in m.classes:
                    return [m.classes[sym]]
                if sym in m.functions:
                    return [m.functions[sym]]
            if spec in self.modules:
                return [self.modules[spec].top_level_id]

        # bare name fallback, e.g. "gallm_coordinator" or "RegulatoryDeck"
        bare = spec.rsplit(".", 1)[-1]
        return list(self.by_bare_name.get(bare, []))

    def node_label(self, node_id: str) -> str:
        if node_id in self.functions:
            fn = self.functions[node_id]
            kind = "async function" if fn.is_async else "function"
            if fn.is_method:
                kind = "async method" if fn.is_async else "method"
            return f"{fn.relpath}:{fn.qualname} ({kind})"
        if node_id in self.classes:
            c = self.classes[node_id]
            return f"{c.relpath}:{c.qualname} (class)"
        if node_id.endswith("::<module>"):
            return node_id.replace("::<module>", " (module top level)")
        return node_id


class _ScopePrepass(ast.NodeVisitor):
    """Single pass over a function/method/module-top-level body (same
    non-descent-into-nested-defs rule as _CallCollector) collecting two
    things needed BEFORE call resolution can run:

      * local_imports: `import`/`from ... import ...` statements that
        appear inside the body itself, not just at module level. This
        codebase does this deliberately in places (local imports to
        avoid a module-level dependency/circular import) -- treating
        only module-level imports as real would silently miss those
        call edges.
      * local_types: `x = SomeClass(...)` direct-constructor
        assignments, for resolving `x.method(...)` later in the same
        body. See model.py's module docstring for the precise (single-
        pass, no dataflow) limits of this heuristic.
    """

    def __init__(self, graph: Graph, mod: ModuleInfo):
        self.g = graph
        self.mod = mod
        self.local_imports: Dict[str, ImportBinding] = {}
        self.local_types: Dict[str, str] = {}

    def visit_FunctionDef(self, node):  # noqa: N802
        pass

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        pass

    def visit_ClassDef(self, node):  # noqa: N802
        pass

    def visit_Import(self, node: ast.Import):  # noqa: N802
        self.g._collect_import(self.mod, node, self.local_imports)

    def visit_ImportFrom(self, node: ast.ImportFrom):  # noqa: N802
        self.g._collect_import(self.mod, node, self.local_imports)

    def visit_Assign(self, node: ast.Assign):  # noqa: N802
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            cls_id = self.g._resolve_constructor(self.mod, node.value)
            if cls_id:
                self.local_types[node.targets[0].id] = cls_id
        self.generic_visit(node)


class _CallCollector(ast.NodeVisitor):
    """Visits a function/method/module-top-level body, resolving every
    ast.Call site, WITHOUT descending into nested FunctionDef/
    AsyncFunctionDef/ClassDef bodies (those are their own graph nodes,
    resolved independently -- see _resolve_calls_in_module)."""

    def __init__(self, graph: Graph, mod: ModuleInfo, caller_id: str,
                 owner_cls: Optional[ClassNode], local_types: Dict[str, str],
                 imports: Dict[str, ImportBinding]):
        self.g = graph
        self.mod = mod
        self.caller_id = caller_id
        self.owner_cls = owner_cls
        self.local_types = local_types
        self.imports = imports

    def visit_FunctionDef(self, node):  # noqa: N802 - ast visitor naming
        pass  # nested def: separate node, don't descend

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        pass

    def visit_ClassDef(self, node):  # noqa: N802
        pass

    def visit_Lambda(self, node):  # noqa: N802
        # Lambda bodies count as belonging to the enclosing scope --
        # they're not named, independently-called graph nodes here.
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):  # noqa: N802
        self._resolve_call(node)
        self.generic_visit(node)

    def _resolve_call(self, node: ast.Call) -> None:
        func = node.func

        if isinstance(func, ast.Name):
            self._resolve_name_call(node, func.id)
            return

        if isinstance(func, ast.Attribute):
            self._resolve_attr_call(node, func)
            return

        # func is itself a Call, Subscript, IfExp, etc: e.g.
        # handlers[name](...), get_callback()(...), (a if b else c)(...).
        # Classic dynamic-dispatch shape called out in the spec.
        self.g.dynamic_sites.append(DynamicSite(
            caller_id=self.caller_id, lineno=node.lineno,
            reason="call target is not a static name/attribute (e.g. subscript or call result)",
            snippet=_safe_unparse(node),
        ))

    def _resolve_name_call(self, node: ast.Call, name: str) -> None:
        # 1. local function/class in the same module.
        if name in self.mod.functions:
            self.g.add_edge(self.caller_id, self.mod.functions[name], node.lineno)
            return
        if name in self.mod.classes:
            self._edge_to_constructor(node, self.mod.classes[name])
            return
        # 2. imported name.
        if name in self.imports:
            self._resolve_via_import(node, self.imports[name], [])
            return
        # 3. getattr(obj, "literal", ...) -- resolve by literal name across
        # the whole tree as a *candidate*, never a confirmed edge.
        if name == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            literal = node.args[1].value
            for target_id in self.g.by_bare_name.get(literal, []):
                self.g.add_dynamic_candidate(self.caller_id, target_id)
            self.g.dynamic_sites.append(DynamicSite(
                caller_id=self.caller_id, lineno=node.lineno,
                reason=f"getattr(..., \"{literal}\") -- candidate edges added, not confirmed",
                snippet=_safe_unparse(node),
            ))
            return
        if name in ("getattr", "importlib", "eval", "exec") or name in BUILTIN_NAMES:
            return  # builtin/stdlib, external, no node to link to
        if name in self.local_types:
            # e.g. a variable shadowing a name we couldn't otherwise place;
            # local_types only maps NAME -> class from constructor calls,
            # calling the bare name itself isn't that pattern -- ignore.
            pass
        # unresolved: a parameter, a closure var, or something genuinely
        # dynamic. Not flagged individually (too noisy at Name-call
        # granularity for ordinary local callables); covered in aggregate
        # by the module's own external-import/undefined-name count if
        # ever needed. Nothing added.

    def _edge_to_constructor(self, node: ast.Call, class_id: str) -> None:
        cls = self.g.classes[class_id]
        init_id = cls.methods.get("__init__")
        target = init_id or class_id
        self.g.add_edge(self.caller_id, target, node.lineno)

    def _resolve_via_import(self, node: ast.Call, binding: ImportBinding, rest_after_name: List[str]) -> None:
        if binding.kind == "module":
            full_mod = binding.target_dotted
            if full_mod in self.g.modules and not rest_after_name:
                # `import module_x; module_x(...)` -- unusual (calling a
                # module), nothing sensible to link to.
                return
            return
        # kind == "attr": binding.target_dotted is the module, binding.attr the symbol
        target_mod_dotted = binding.target_dotted
        attr = binding.attr
        if target_mod_dotted in self.g.modules and attr:
            target_mod = self.g.modules[target_mod_dotted]
            if attr in target_mod.classes:
                self._edge_to_constructor(node, target_mod.classes[attr])
                return
            if attr in target_mod.functions:
                self.g.add_edge(self.caller_id, target_mod.functions[attr], node.lineno)
                return
        # external (stdlib/third-party) or an unresolved symbol: no edge.

    def _resolve_attr_call(self, node: ast.Call, func: ast.Attribute) -> None:
        chain = flatten_attr_chain(func)
        if chain is None:
            self.g.dynamic_sites.append(DynamicSite(
                caller_id=self.caller_id, lineno=node.lineno,
                reason="attribute call base is not a static name (e.g. chained call result)",
                snippet=_safe_unparse(node),
            ))
            return

        head, rest = chain[0], chain[1:]

        # self.method(...) / self.attr.method(...) / cls.method(...)
        if head in ("self", "cls") and self.owner_cls is not None:
            if len(rest) == 1:
                method_name = rest[0]
                target = self._find_method_in_hierarchy(self.owner_cls, method_name)
                if target:
                    self.g.add_edge(self.caller_id, target, node.lineno)
                    return
                self.g.dynamic_sites.append(DynamicSite(
                    caller_id=self.caller_id, lineno=node.lineno,
                    reason=f"self.{method_name}(...) not found on {self.owner_cls.qualname} or its resolvable bases (may be inherited from an external base, or set dynamically)",
                    snippet=_safe_unparse(node),
                ))
                return
            if len(rest) == 2:
                attr_name, method_name = rest
                attr_cls_id = self.owner_cls.instance_attr_types.get(attr_name)
                if attr_cls_id:
                    target = self._find_method_in_hierarchy(self.g.classes[attr_cls_id], method_name)
                    if target:
                        self.g.add_edge(self.caller_id, target, node.lineno)
                        return
            self.g.dynamic_sites.append(DynamicSite(
                caller_id=self.caller_id, lineno=node.lineno,
                reason=f"self.{'.'.join(rest)}(...) -- attribute type not inferable statically",
                snippet=_safe_unparse(node),
            ))
            return

        # local_var.method(...) where local_var was directly assigned
        # `local_var = SomeClass(...)` earlier in this same function.
        if head in self.local_types and len(rest) == 1:
            target = self._find_method_in_hierarchy(self.g.classes[self.local_types[head]], rest[0])
            if target:
                self.g.add_edge(self.caller_id, target, node.lineno)
                return

        # module_alias.func(...) / module_alias.Class.method(...) /
        # module_alias.submodule.func(...)
        if head in self.imports:
            self._resolve_import_chain_call(node, self.imports[head], rest)
            return

        if head in self.mod.classes and rest:
            # ClassName.staticmethod_or_classmethod(...) within the same module
            if len(rest) == 1:
                target = self._find_method_in_hierarchy(self.g.classes[self.mod.classes[head]], rest[0])
                if target:
                    self.g.add_edge(self.caller_id, target, node.lineno)
                    return

        # Unresolved: attribute call on something whose type this tool
        # doesn't track (a function parameter, a loop variable over an
        # unknown collection, a dict value, ...). Honest dynamic marker,
        # not a guess.
        self.g.dynamic_sites.append(DynamicSite(
            caller_id=self.caller_id, lineno=node.lineno,
            reason=f"'{'.'.join(chain)}(...)' -- receiver type not inferable statically",
            snippet=_safe_unparse(node),
        ))

    def _resolve_import_chain_call(self, node: ast.Call, binding: ImportBinding, rest: List[str]) -> None:
        if not rest:
            return
        if binding.kind == "module":
            full = binding.target_dotted if len(rest) == 1 else ".".join([binding.target_dotted] + rest[:-1])
            leaf = rest[-1]
            if full in self.g.modules:
                m = self.g.modules[full]
                if leaf in m.classes:
                    self._edge_to_constructor(node, m.classes[leaf])
                    return
                if leaf in m.functions:
                    self.g.add_edge(self.caller_id, m.functions[leaf], node.lineno)
                    return
            return  # external module, nothing to link
        # kind == "attr": binding.target_dotted/attr already picked out one
        # symbol from a module; `rest` here means Class.method(...) off it.
        if binding.target_dotted in self.g.modules and binding.attr:
            m = self.g.modules[binding.target_dotted]
            if binding.attr in m.classes and len(rest) == 1:
                target = self._find_method_in_hierarchy(self.g.classes[m.classes[binding.attr]], rest[0])
                if target:
                    self.g.add_edge(self.caller_id, target, node.lineno)
                    return
        # external / unresolved: no edge (not flagged individually -- an
        # attribute chain off a third-party import, e.g.
        # `httpx.Client(...).get(...)`, is out of scope by design).

    def _find_method_in_hierarchy(self, cls: ClassNode, method_name: str, _seen: Optional[Set[str]] = None) -> Optional[str]:
        if _seen is None:
            _seen = set()
        if cls.id in _seen:
            return None
        _seen.add(cls.id)
        if method_name in cls.methods:
            return cls.methods[method_name]
        for base_id in cls.resolved_bases:
            base = self.g.classes.get(base_id)
            if base:
                found = self._find_method_in_hierarchy(base, method_name, _seen)
                if found:
                    return found
        return None


def _safe_unparse(node: ast.AST) -> str:
    try:
        text = ast.unparse(node)
    except Exception:
        return "<unparseable>"
    return text if len(text) <= 120 else text[:117] + "..."
