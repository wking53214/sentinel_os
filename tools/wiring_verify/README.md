# wiring_verify

A static wiring-verification tool for sentinel_os. Given a claim like
"X is wired into production" or "Y is unreachable", it answers
mechanically from an AST-derived call graph, instead of a grep
spot-check or trusting a docstring/README.

This exists because this repo has a documented recurring defect shape
(see `COMPLIANCE.md` and `docs/architecture/`): real, extensively
tested code that no entry point ever constructs. Tests passing and a
module existing are not evidence it runs. This tool checks the one
thing that can actually be checked without running the system: is
there a resolvable static call path.

## What this tool answers

**"Is there a static call path from entry point E to target T?"**
Four possible answers, never collapsed into yes/no:

| Status | Meaning |
|---|---|
| `REACHABLE` | A confirmed static call chain exists, OR the target is itself part of the entry point's declared root set (see below) -- the report always says which. |
| `UNVERIFIABLE_STATICALLY` | Could not be confirmed *or* ruled out: reachable only through a `getattr(obj, "literal_name")` candidate, or the target is a decorator-registered handler (route, hook, plugin) whose real caller lives in a framework this tool doesn't trace. |
| `UNREACHABLE` | The target exists in the parsed tree, but no static or dynamic-candidate path was found from this entry point. |
| `NOT_FOUND` | The target string doesn't match anything anywhere in the parsed source tree -- distinct from `UNREACHABLE`. A module that was deleted, or a name that only ever existed in a doc, reports `NOT_FOUND`. |

## What this tool does NOT answer

- **"Does this code actually execute at runtime?"** No. Static call
  paths are a necessary, not sufficient, condition. A path can exist
  and still never execute (a conditional that's always false, a
  feature flag, an exception that always fires first). This tool
  cannot see any of that.
- **Anything behind genuine dynamic dispatch.** `getattr(obj,
  computed_name)`, `importlib.import_module(computed)`,
  `globals()[name]`, a dict-of-callables looked up by a runtime value,
  a decorator that registers a handler with a framework (FastAPI
  routes, signal handlers, plugin registries) -- none of these have a
  resolvable static target. They are marked `UNVERIFIABLE_STATICALLY`
  and reported as dynamic call sites (`sweep`'s dynamic-sites section,
  or per-target in `query`'s detail column). They are never silently
  folded into `REACHABLE` or `UNREACHABLE`.
- **Type-correctness of what it does infer.** Local-variable and
  `self.attr` type inference (see below) is a single-pass heuristic:
  `x = SomeClass(...)` then `x.method(...)` in the same function
  resolves; `x` reassigned across a branch, passed in as a parameter,
  or pulled out of a collection does not, and is honestly marked as an
  unresolved dynamic call site rather than guessed.
- **Whether an entry point is the RIGHT thing to call an entry point.**
  `default_entry_points.txt` is a human judgment call this tool cannot
  make. It executes what's declared; it doesn't audit the declaration.

## Entry point semantics

A **function-scoped** entry (`module.py:function_name` or
`module.py:ClassName`) has exactly one root (or, for a class, its
public methods) -- only genuine call edges count toward reachability
from it.

A **module-scoped** entry (`module.py`, no `:target`) has no single
knowable "first thing called" without an actual runtime harness, so
its root set is deliberately **over-inclusive**: `main()` if present,
every public top-level function, every public method of every public
top-level class, and the module's own top-level statements (which
includes an `if __name__ == "__main__":` body -- exactly what running
the file as a script executes). This means a module-scoped entry can
report something `REACHABLE` with a one-node "chain" (the target *is*
a root) rather than a real call path -- the report always says which
kind of `REACHABLE` it found, specifically so this can't be misquoted
as a proven call chain when it's actually "this function is part of
the entry module's exported surface and has no confirmed caller in
this repo" (the honest description of, e.g., a FastAPI route handler).

## Two separate reachability questions

`sweep` (and `query`) always compute and report these as **separate
sections**, never merged:

1. **Reachable from any declared entry point** (`default_entry_points.txt`
   or `--entries-file`) -- "is this wired into *something* real".
2. **Reachable from what's actually deployed** (`--deployed`, parsed
   from `Dockerfile` CMD/ENTRYPOINT, `docker-compose*.yml` service
   `command:` overrides falling back to the Dockerfile CMD, and
   `k8s/**/*.yaml` / `Deploy/k8s/**/*.yaml` container `command`/`args`
   falling back the same way) -- "is this wired into what a user
   actually gets when they run this repo".

A module can be a perfectly valid standalone entry point (question 1:
yes) and still not be what's deployed (question 2: no) -- e.g.
`sentinel_worker.py` and `api_server_v2.py` in this repo are real,
callable entry points, but the Dockerfile/compose/k8s configs all
resolve to `api_server_resilient.py`. Reporting one number here would
erase that distinction; the tool never does.

## Usage

```
python3 cli.py query --root /path/to/sentinel_os/sentinel_os \
    --target regulatory_deck.RegulatoryDeck

python3 cli.py query --root /path/to/sentinel_os/sentinel_os \
    --target sentinel_worker.py:main

python3 cli.py sweep --root /path/to/sentinel_os/sentinel_os --deployed

python3 cli.py sweep --root /path/to/sentinel_os/sentinel_os \
    --deployed --verbose --out report.md
```

`--root` must point at the actual Python source root (the directory
whose files import each other with bare/dotted names, e.g.
`sentinel_os/sentinel_os/`, not the outer git repo root). `Tests/` is
always excluded from the source root at parse time, per spec -- test
files are parsed *separately* (`coverage.py`) only to answer "does a
test reference this", never folded into the reachability graph itself
(otherwise every well-tested-but-dead module would falsely show
`REACHABLE` from its own test).

Target spec forms accepted by `query --target`:
- `module.py:function_name` or `module.py:ClassName`
- `dotted.module.path.Symbol` (e.g. `regulatory_deck.RegulatoryDeck`)
- a bare name (e.g. `gallm_coordinator`) -- searched across every
  module, class, function, and method name in the tree; reports every
  match, or `NOT_FOUND` if there are none.

## How call resolution works (and its limits)

For every function/method/module-top-level body, every `ast.Call` is
resolved, in order:

1. Direct name matching a same-module function or class (constructor
   calls resolve to `__init__`, or the class node if there's no
   explicit `__init__`).
2. Through the module's own `import` / `from ... import ...` bindings,
   including `as` aliases and relative imports (`from . import x`,
   `from ..pkg import y`) -- resolved by walking the actual repo
   directory structure, not by trusting names.
3. `self.method(...)` / `cls.method(...)` -- resolved against the
   owning class and its statically-resolvable base classes (a base
   from an external/unresolvable import stops the walk there, honestly).
4. `self.attr.method(...)` / `local_var.method(...)` -- resolved only
   when `attr`/`local_var` was directly assigned from a constructor
   call somewhere in the class's methods (for `self.attr`) or the same
   function body (for a local var). No cross-function or cross-branch
   dataflow.
5. `getattr(obj, "literal_string")` -- produces a *candidate* edge to
   every symbol in the whole tree named `literal_string`, tagged
   dynamic, never a confirmed edge.
6. Anything else with a non-static call target (`handlers[name](...)`,
   `get_callback()(...)`, `getattr(obj, computed_name)`,
   `importlib.import_module(...)`) -- recorded as a dynamic call site,
   contributes no confirmed edge.

Calls that resolve into a module outside the parsed source root
(stdlib, third-party packages) simply produce no edge and no warning
-- that's the correct "out of scope" outcome, not a gap.

## Decorator handling

Functions decorated with anything **not** in a small structural
allowlist (`staticmethod`, `classmethod`, `property`, `abstractmethod`,
`functools.wraps`, `contextmanager`, `dataclass`, `overload`,
`cached_property`, `final`) are flagged `dynamically_registered=True`.
This covers route decorators (`@app.get(...)`), retry/cache wrappers,
and any project-local registration decorator. A flagged function with
no confirmed static caller reports `UNVERIFIABLE_STATICALLY`, not
`UNREACHABLE` -- the tool assumes there's a real caller (the framework)
it just can't trace, rather than either believing or dismissing it.

## Known false-negative / false-positive shapes (read before citing a result)

- **False "UNREACHABLE"**: anything reached only through data-carrying
  dynamic dispatch this tool doesn't attempt to simulate (e.g. a
  message-queue payload whose `type` field selects a handler via a
  dict literal built dynamically, or reflection through a string built
  from concatenation rather than a single literal).
- **False "REACHABLE"**: a module-scoped entry point's over-inclusive
  root set (see above) counts a function as a root just because it's
  public and top-level in that file, even if nothing ever actually
  calls it that way. Always check the `detail` column/field before
  treating a `REACHABLE` result as a proven call chain -- a one-node
  chain means "declared root", not "confirmed call".
- **Multiple inheritance / MRO**: base-class method resolution here is
  a straightforward depth-first walk of statically-resolved bases, not
  full C3 linearization. For a small, mostly-single-inheritance
  codebase like this one it matches Python's actual MRO in every case
  checked; a `super().method()` diamond could in principle resolve to
  a different override than the one that would actually be called.

## Files

- `model.py` -- AST parsing, import resolution, call-graph construction.
- `reachability.py` -- BFS reachability, entry-point root resolution, status classification.
- `deploy_config.py` -- Dockerfile/docker-compose/k8s parsing.
- `coverage.py` -- test-file symbol references (kept out of the main graph; see above).
- `cli.py` -- `query` / `sweep` commands.
- `default_entry_points.txt` -- the declared entry-point list for this repo.
- `tests/test_acceptance.py` -- regression tests against known-true facts about this repo, checked by hand.
