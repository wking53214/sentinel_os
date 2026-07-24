# Domain/

Canonical state-representation types for the standalone simulator
(`iceberg_complete_simulator.py`) and its supporting RL/routing engines
(`Engines/`, `Model/`, `Sim/`). Not used by the production governance path
(`production_harness.py`, `sentinel_worker.py`, `api_server*.py`) --
that path has its own request/decision types and doesn't import from here.

## Files

- **`CallerState.py`** -- `CallerState` (a caller's intent, emotion,
  Bayesian posterior over intents, and per-step `DynamicState`: perceived
  wait, frustration) and `DynamicState`. Plain dataclasses; mutated only
  by simulator step updates, never by these classes themselves.
  `snapshot()`/`to_dict()` are JSON-safe serializations for telemetry.

- **`Emotion.py`** -- `Emotion` enum: `NEUTRAL`, `IMPATIENT`,
  `FRUSTRATED`, `ANGRY` (values 0-3, fixed order). Used as the emotion
  axis of `CallerState` and as an RL input feature (`Engines/rl_ppo_adaptive.py`
  one-hot encodes it via `encode_state`).

- **`Intent.py`** -- `Intent` enum: `BILLING`, `TECH_SUPPORT`, `CANCEL`,
  `UPGRADE`, `COMPLAINT`, `SALES`, `GENERAL`, `OTHER` (values 0-7, fixed
  order). Same role as `Emotion.py`: routing/RL input feature and
  `CallerState`'s intent axis.

- **`QueueState.py`** -- `QueueState`: one queue's operational metrics
  (`active_calls`, `staffing`, `target_service_level`,
  `abandonment_rate`). `apply_delta()`/`update_active_calls()` both clamp
  at zero (staffing and queue depth can't go negative). Updated by
  `Sim/Simulator.py` and the staffing RL trainer.

## What these are, and aren't

Plain dataclasses/enums with no side effects and no dependency on the
governance/ledger machinery. `Emotion`/`Intent` guarantee a fixed
enumeration order (the ordinal value backing `.list()`/`.index()`, used
for one-hot encoding) -- that ordering is a real invariant other code
depends on for replay consistency, not just documentation flavor.

`Build_Graph.py` (in `Model/`, not here) already lists several concrete
gaps in the code's own review notes -- mutable `GraphNode.neighbors`,
no topology version identifier -- worth reading if you're touching graph
construction; nothing analogous is currently flagged in this directory.
