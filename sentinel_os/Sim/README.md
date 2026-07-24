# Sim/

The simulator's execution loop: one caller's single step, and running
many callers in parallel.

## Files

- **`Simulator.py`** -- `Simulator.step(caller, start_node)`: the fixed
  per-step pipeline -- routing decision, then (if a `governance` object
  is attached) the routing decision is passed through it, then a
  staffing proposal, a Bayesian intent-posterior update, the queue
  transition, and latent-state evolution -- in that order, every step.
  Raises `RuntimeError` past `max_steps` (default 815) as a hard loop
  guard. Returns a structured telemetry packet (caller id, intent,
  emotion, routing/staffing/bayes output, `next_node`) rather than
  mutating the caller in place.

- **`cluster_runner.py`** -- `ClusterRunner`: runs a batch of callers
  through `Simulator.step` concurrently (`ThreadPoolExecutor`,
  default 8 workers). `run_batch()` submits every caller in the batch
  and collects results; `run_episode()` wraps `run_batch()` for RL
  callers that want one structured episode return instead of a bare
  list. `_run_single()` does the actual per-caller work and telemetry
  recording; `ClusterRunner` itself holds no state beyond its
  `simulator`/`telemetry` handles.

## One correction to the code's own claim

`run_batch()`'s docstring says "deterministic ordering: futures
completed in stable order." That's not accurate as written: it collects
results via `concurrent.futures.as_completed(futures)`, which yields
futures in actual **completion** order, not submission order --
real thread-scheduling timing, not something pinned to be stable run to
run. In practice, with this simulator's uniformly fast per-caller work,
completion order will often track submission order closely, but that's
an artifact of the work being fast and uniform, not a guarantee the code
makes. If `run_batch()`'s output order needs to be genuinely
deterministic (e.g. for replay-exact comparisons), sort results by
`caller_id` (or another stable batch-provided key) after collection
rather than relying on completion order.
