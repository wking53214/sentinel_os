# Apply document: the conservation boundary on the current Conservation_Kernel

Delivered on branch `claude/stack-e2e-integration-test-y773sp` of `wking53214/sentinel_os`, as Step 2 of a two-step
move; Step 1 (the episode source registers evidence that is about what cites it) is already on `main`
(`00c7e45`, PR #140). Take "this change" throughout as Step 2.

---

## 1. What changed, in plain words

The kernel the boundary uses moved from `7b08c79` to `cc42d3f`. That kernel refuses to take evidence from
just anyone, and refuses a kernel built without saying how roots and evidence are guarded. So:

- `conservation/evidence_guard.py` (new): `EpisodeEvidenceGuard` permits only the records
  `build_episode_source` produces for this episode, with the subject, kind, observer, episode and domain they record.
  `episode_source.expected_evidence` is the single list both the builder and the guard use.
- `conservation/boundary.py`: the registry is built with the guard on every path. The no-events path now states
  `allow_unguarded_roots=True` itself, where before it relied on a default hard-coded in the vendored gateway.
  Behaviour is unchanged; the gap is named, not closed.
- `conservation/transport/transport.py` (vendored): the fallback kernel takes the evidence opt-out only when the
  registry has no evidence guard (what that path did before). Recorded as drift in `transport/PROVENANCE.md`.
- `requirements.txt`: kernel pinned to `cc42d3f` (was `7b08c79`).
- Docs: `conservation/CONFORMANCE.md`, `conservation/transport/PROVENANCE.md`, this note.

## 2. What does not change

No ledger hash or verdict receipt: `ledger_postgres.py` discards the boundary's result and uses only whether it
raised. The judgment (`judgment.py`) and `EpisodeRootGuard` are untouched. The authorizations rule (needs
`trusted_humans`) does not apply: the boundary adds none.

## 3. Steps

1. Already merged: `episode_source.py` registers evidence about what cites it (PR #140).
2. This change, one commit: the guard, the boundary, the vendored fallback, the pin, the tests and the docs above.

## 4. Pre-existing tests edited

One, edited only for the behaviour this step changes (a kernel built directly now needs an evidence guard or the
explicit opt-out):

- `Tests/test_episode_root_guard.py::test_a_kernel_with_the_guard_refuses_a_root_that_was_not_attested`: the
  `ConservationKernel(...)` call gains `allow_unguarded_evidence=True`. Nothing else in it changed.

New: `Tests/test_episode_evidence_guard.py`.

## 5. What this does not prove

That the episode is truthful (the guard says which evidence may enter, not whether it is right), and nothing about
the no-events path, where the root is still unguarded by design. The PostgreSQL ledger tests are the check that the
ledger still writes and refuses correctly; CI runs them.
