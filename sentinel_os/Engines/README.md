# Engines/

One small, self-contained RL implementation used by the standalone
simulator. It doesn't read from or write to the governance ledger --
it trains against synthetic reward signals within a simulator run,
nothing more.

## Files

- **`simple_rl_trainer.py`** -- `SimpleRLTrainer`: an actual (if
  minimal) policy-gradient trainer -- REINFORCE-style with a value
  baseline, not PPO's clipped objective, but this one does learn:
  `collect_trajectory()` records (state, action, reward, done) tuples,
  `update_weights()` computes discounted returns, normalizes them,
  and applies a plain gradient-descent update to both the policy and
  value weight matrices. `seed` is threaded through a single
  `np.random.default_rng(seed)` call that initializes both weight
  matrices -- worth knowing if you're reading the git history, since an
  earlier version re-seeded from the unseeded global RNG two lines
  later, which silently made the `seed` parameter dead (passing the same
  seed twice produced two different policies). Fixed; the current
  version is genuinely seed-reproducible.

## Removed: `rl_ppo_adaptive.py` (25 July 2026)

Previously held `PPORouter`, a stateless cached-weight routing policy
whose name claimed PPO but implemented none of it (no clipped
surrogate objective, no advantage estimation, no training loop --
fixed random weights from a hardcoded seed, same output every run).
Flagged by an independent repository review (24 Jul 2026, commit
67eea1d9) as heuristics dressed up as ML.

Removed rather than relabeled: it was never imported by the
production path or the real simulator (`iceberg_complete_simulator.py`
uses `SimpleRLTrainer`, above), and its only live consumer was one
test asserting a constructor argument got stored. Confirmed via
repo-wide search before deletion -- nothing else depended on it.

If real PPO (or something beyond `SimpleRLTrainer`) is ever wanted
here, `SimpleRLTrainer` is the starting point, not this file -- it's
the one that already does the real thing, just simply.

