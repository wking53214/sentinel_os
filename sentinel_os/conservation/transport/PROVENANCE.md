# Provenance — `conservation/transport/`

Vendored from **`GEMS/transport/gems_transport/`** at GEMS commit
`44470ff8728a0914498d79f715dd736ba3c69c78` (2026-09-03).

It originated as an unwired "version B" GEMS direction — an enforced output
boundary around `conservation_kernel` plus a 20-attack hostile corpus (all 20
blocked). Salvaged into GEMS 2026-08-28; not GEMS's mission, and its own
`PROVENANCE.md` there flags it as unreconciled with `src/gems/`. Sentinel is
its first real consumer.

## Files taken (near-verbatim)

| here | from | md5 at copy |
|---|---|---|
| `contracts.py` | `gems_transport/contracts.py` | `88a51e98f9435902f41842ac060478e8` |
| `artifact.py` | `gems_transport/artifact.py` | `8c5d7be08fea683e5019aada645a6652` |
| `errors.py` | `gems_transport/errors.py` | `5cb7eed8352b940bc2c59984718e5535` |
| `registry.py` | `gems_transport/registry.py` | `df0a10065995a5604036e0e11152acd2` |
| `transport.py` | `gems_transport/transport.py` | `49f078364219460afaa19e5c8bc558dc` |
| `builder.py` | `gems_transport/reference_gems/base.py` | `9c5fd432378f9430909c7f98cf96d312` |

## Changes on the way in

- `builder.py` (was `reference_gems/base.py`): `from ..artifact` / `from ..contracts`
  → `from .artifact` / `from .contracts` (moved up one package level). No logic change.
- Not taken: `pipeline.py` (multi-stage chains — `_write_decision` is one
  transformation), `reference_gems/*` concrete gems, `tie_adapter.py` (Sentinel
  writes its own source adapter — `conservation/episode_source.py`),
  `experiments/`.

## Sentinel-specific glue (not vendored)

- `conservation/episode_source.py` — `Episode` → a root source `Artifact`.
- `conservation/judgment.py` — the governance judgment as a `BaseGem` transformer.
- `conservation/boundary.py` — the single fail-closed entry point `_write_decision` calls.

## Re-sync

If `gems_transport` changes upstream, re-copy the six files and re-apply the
one import edit. The vendored files import only `conservation_kernel` + stdlib.
