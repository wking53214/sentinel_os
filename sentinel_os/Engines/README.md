# Engines/

Two small, self-contained RL implementations used by the standalone
simulator. Neither reads from or writes to the governance ledger --
they train against synthetic reward signals within a simulator run,
nothing more.

## Files

- **`rl_ppo_adaptive.py`** -- `PPORouter`: a stateless, cached-weight
  routing policy. `choose_action(caller, node_id)` encodes the caller's
  intent/emotion/dynamic-state one-hot, runs it through a cached random
  projection (`_get_weights`, keyed by `(rows, cols, seed)` so the same
  shape+seed always gets the same weights), applies a small wait-time
  penalty per candidate next-node, and returns the argmax action plus
  its log-probability. "PPO" in the name is aspirational relative to the
  current implementation: there's no clipped surrogate objective, no
  advantage estimation, no training loop here at all -- this is a fixed,
  untrained random policy with a deterministic wait-time adjustment
  layered on top, not a trained PPO agent. `PPOConfig.seed_policy` /
  `seed_value` (both `815`) fix the weights, so runs are reproducible.

- **`simple_rl_trainer.py`** -- `SimpleRLTrainer`: an actual (if
  minimal) policy-gradient trainer -- REINFORCE-style with a value
  baseline, not PPO's clipped objective either, but this one does learn:
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

## Naming note

Despite the filename, `rl_ppo_adaptive.py`'s `PPORouter` doesn't
actually implement PPO (see above) -- if you're looking for the file
that trains something, that's `simple_rl_trainer.py`.
