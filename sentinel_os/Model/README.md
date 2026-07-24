# Model/

The routing graph topology for the standalone simulator
(`iceberg_complete_simulator.py`). One file, one job: build and validate
the fixed node/edge structure calls route through.

## Files

- **`Build_Graph.py`** -- `GraphNode`, `RoutingGraph`, `GraphBuilder`
  (fluent builder), and `build_graph()`, which constructs the actual
  topology used: `root -> intent_menu -> {billing,tech,cancel,upgrade,
  complaint,sales,general}_queue -> {same}_agent -> exit`. `RoutingGraph.validate()`
  checks every neighbor reference resolves to a real node and raises on
  a dangling edge -- called automatically at the end of `GraphBuilder.build()`,
  so a malformed topology never reaches the simulator.

## Disclosed gaps (from the file's own review notes, not re-derived here)

`Build_Graph.py` documents several unresolved items directly in-file
rather than leaving them silent:

- `GraphNode.neighbors` is a plain mutable `list`, not frozen -- nothing
  stops a caller from mutating it after construction, which would be a
  replay-safety risk if anything downstream ever did that (nothing
  currently does).
- `validate()` only checks forward references (node -> neighbor exists),
  not reverse-edge consistency or orphaned nodes.
- No topology version identifier, so two `RoutingGraph` instances can't
  be compared for "same topology" without a manual field-by-field diff.
- No cycle detection (cycles are valid for IVR menus generally, but
  currently unmonitored -- a misconfigured graph could route a caller
  in circles with nothing to flag it).

None of these are correctness failures in the topology this file
actually builds (which is acyclic and fully connected); they're gaps
that would matter if `build_graph()`'s hardcoded topology were ever
replaced with a data-driven or user-editable one.
